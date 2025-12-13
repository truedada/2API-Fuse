# app/services/chat.py
from fastapi import HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from loguru import logger
import json
import asyncio
import time
import uuid
from typing import Dict, Any, Optional, List

from app.schemas.chat import ChatCompletionRequest, ModelListResponse, ModelCard
from app.core.redis.cache import CacheService
from app.adapters.factory import AdapterFactory
from app.repositories.apikey import ApiKeyRepository
from app.repositories.channel import ChannelRepository
from app.services.usage_log import UsageLogService
from app.core.exceptions.definitions import (
    PermissionDenied,
    ServiceUnavailable,
    ExternalServiceError,
    NotFound
)

class ChatService:

    # 定义自动封禁的错误阈值
    MAX_CONSECUTIVE_ERRORS = 5

    @staticmethod
    async def _handle_channel_error(channel_id: int, error_msg: str, model_name: str = None):
        """
        处理渠道错误：计数 + 自动封禁判断
        :param model_name: 触发错误的模型名称（用户侧），用于精确清除缓存池
        """
        count = await CacheService.incr_channel_error(channel_id)
        logger.warning(f"Channel {channel_id} 发生错误 ({count}/{ChatService.MAX_CONSECUTIVE_ERRORS}): {error_msg}")

        if count >= ChatService.MAX_CONSECUTIVE_ERRORS:
            logger.error(f"Channel {channel_id} 错误次数超标，正在执行自动禁用...")
            try:
                repo = ChannelRepository()
                # 1. 数据库禁用
                await repo.disable_channel(channel_id, error_msg=f"因 {count} 次连续调用出错而被自动禁用。最后一次报错： {error_msg}")

                # 2. Redis 紧急熔断：从当前模型的可用池中立即移除
                # 这是为了防止 sync_channel 计算出的模型列表不包含当前模型别名，导致残留
                if model_name:
                    logger.warning(f"正在从模型池 {model_name} 中强制移除 Channel {channel_id}")
                    await CacheService.remove_from_pool(model_name, channel_id)

                # 3. Redis 全量清理 (调用 sync_channel)
                await CacheService.sync_channel(channel_id)

            except Exception as e:
                logger.error(f"自动禁用 Channel {channel_id} 失败: {e}")

    # 【新增】获取系统模型列表
    @staticmethod
    async def get_model_list() -> ModelListResponse:
        """
        获取系统支持的所有模型，并包装为 OpenAI 格式
        """
        # 从 Cache Service 获取聚合后的模型名称列表
        models = await CacheService.get_all_system_models()

        # 转换为 ModelCard 对象
        card_list = [
            ModelCard(id=m) for m in models
        ]

        return ModelListResponse(data=card_list)

    @staticmethod
    async def create_chat_completion(
        api_key: str,
        request: ChatCompletionRequest,
        background_tasks: BackgroundTasks
    ):
        user_model_name = request.model
        
        # --- [NEW] 初始化计时与追踪 ---
        start_time = time.time()
        trace_id = str(uuid.uuid4())
        # ----------------------------

        # -----------------------------------------------------------
        # 1. 鉴权与扣费 (Authentication & Billing)
        # -----------------------------------------------------------
        allowed = await CacheService.atomic_deduct_quota(api_key, cost=1)

        if not allowed:
            # ### 回捞逻辑 (Fallback) ###
            # Redis 中可能数据为空或不一致，尝试查数据库确认
            repo = ApiKeyRepository()
            key_obj = await repo.get_by_key(api_key)
            if not key_obj or not key_obj.is_active:
                raise PermissionDenied(detail="API Key 无效或被禁用")

            # 将数据库数据同步回 Redis
            await CacheService.sync_apikey(key_obj)

            if not key_obj.has_quota:
                 raise PermissionDenied(detail="API Key 余额不足")

            # 再次尝试 Redis 扣费
            if not await CacheService.atomic_deduct_quota(api_key, cost=1):
                 raise PermissionDenied(detail="API Key 余额不足")

        # -----------------------------------------------------------
        # 2. 寻址 (Routing)
        # -----------------------------------------------------------
        # 从 Redis Pool 中获取一个可用的 Channel ID
        channel_conf = await CacheService.get_best_channel(user_model_name)
        if not channel_conf:
            logger.warning(f"模型 {user_model_name} 无可用渠道")
            raise ServiceUnavailable(detail=f"当前模型 {user_model_name} 无可用渠道")

        channel_id = channel_conf.get('id')
        logger.info(f"[{trace_id}] 路由: {user_model_name} -> 渠道ID: {channel_id}")

        # -----------------------------------------------------------
        # 3. 初始化适配器 (Adapter Init)
        # -----------------------------------------------------------
        try:
            adapter = AdapterFactory.get_adapter(
                adapter_type=channel_conf.get("adapter", "openai"),
                config=channel_conf
            )
        except Exception as e:
            logger.error(f"适配器初始化失败: {e}")
            raise ServiceUnavailable(detail="内部配置错误: 适配器初始化失败")

        # --- 注入凭证更新回调逻辑 ---
        async def credential_update_callback(new_creds: Dict[str, Any]):
            logger.info(f"Adapter 触发凭证更新 Channel ID: {channel_id}")
            try:
                channel_repo = ChannelRepository()
                await channel_repo.update_credentials(channel_id, new_creds)
                await CacheService.sync_channel(channel_id)
                logger.info(f"凭证更新并同步成功 Channel ID: {channel_id}")
            except Exception as e:
                logger.error(f"凭证更新回调失败: {e}")

        adapter.set_credential_update_callback(credential_update_callback)

        # -----------------------------------------------------------
        # 4. 准备请求参数 (Params Prep)
        # -----------------------------------------------------------
        model_map = channel_conf.get("model_map") or {}
        # 映射：用户请求的模型 -> 渠道实际需要的模型名
        upstream_model = model_map.get(user_model_name, user_model_name)

        if upstream_model != user_model_name:
            logger.debug(f"执行模型映射: {user_model_name} -> {upstream_model}")

        # 【核心修改】获取该 Adapter 对该模型的自定义后端扣费权重
        backend_cost = adapter.get_backend_usage_cost(upstream_model)
        if backend_cost != 1:
            logger.info(f"[{trace_id}] 模型 {upstream_model} 后端权重: {backend_cost}")

        req_dict = request.model_dump(exclude_none=True)
        req_dict['model'] = upstream_model

        if 'extra_body' in req_dict and req_dict['extra_body']:
            req_dict['extra_body'] = request.extra_body
        if request.stream_options:
            req_dict['stream_options'] = request.stream_options.model_dump()
        
        # --- [修改] 统一的后台使用记录任务 (变量名已修正) ---
        async def record_usage_task(
            channel_id: int, 
            model_name: str, 
            api_key: str, 
            prompt_tokens: int = 0, 
            completion_tokens: int = 0,
            total_tokens: int = 0,
            is_stream_log: bool = False,
            backend_cost: int = 1 # 【新增】参数
        ):
            # 计算耗时
            duration = int((time.time() - start_time) * 1000)
            
            try:
                # 1. Redis: 记录渠道使用 (触发限流检查)
                # 【修改】传入 cost 参数
                await CacheService.record_channel_usage(channel_id, model_name, cost=backend_cost)
                
                # 2. Redis: 记录 Token 消耗 (用于计费)
                if total_tokens > 0:
                    logger.debug(f"[{trace_id}] 记录请求 Token: {total_tokens}")
                    await CacheService.record_apikey_tokens(api_key, total_tokens)
                
                # 3. [新增] Database: 写入永久日志
                await UsageLogService.log_transaction(
                    api_key_str=api_key,
                    channel_id=channel_id,
                    model_name=model_name,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    duration_ms=duration,
                    is_stream=is_stream_log,
                    trace_id=trace_id
                )

            except Exception as e:
                logger.error(f"后台记录任务失败: {e}")

        # -----------------------------------------------------------
        # 5.A 流式处理 (Streaming)
        # -----------------------------------------------------------
        if request.stream:
            # [修改说明] 移除了此处原本的 background_tasks.add_task(CacheService.record_channel_usage...)
            # 改为在 stream_wrapper 内部，确认请求成功发出（收到第一个 chunk）时再记录。
            # 避免因 Adapter 初始化失败（如 Get Token 失败）而错误地增加调用计数。

            async def stream_wrapper(generator):
                total_tokens_accumulated = 0
                prompt_tokens_cnt = 0
                completion_tokens_cnt = 0
                
                estimated_tokens = 0
                has_error = False # 标记本次请求是否出错
                usage_recorded = False # [修改] 标记是否已记录渠道使用

                try:
                    async for chunk in generator:

                        # [修改] 延迟记录逻辑：收到第一个 chunk 意味着请求已成功发送并建立了连接
                        # 此时记录使用量是安全的，符合"发送到服务器才算调用"的逻辑
                        if not usage_recorded:
                            usage_recorded = True
                            # 使用 asyncio.create_task 异步记录，不阻塞流
                            # 【修改】传入 backend_cost
                            asyncio.create_task(
                                CacheService.record_channel_usage(
                                    channel_id, 
                                    user_model_name, 
                                    cost=backend_cost
                                )
                            )

                        # 尝试解码以在日志中正确显示中文
                        try:
                            # 仅用于 debug 日志展示
                            # log_content = str(chunk).encode('latin1').decode('unicode_escape')
                            pass
                        except Exception:
                            pass
                        
                        yield chunk

                        # ### 尝试从 chunk 中解析 Token 信息 ###
                        if '"usage"' in chunk:
                            try:
                                clean_line = chunk.strip()
                                if clean_line.startswith("data: "):
                                    json_str = clean_line[6:]
                                    if json_str != "[DONE]":
                                        data = json.loads(json_str)
                                        usage_data = data.get("usage", {})
                                        if usage_data:
                                            total_tokens_accumulated = usage_data.get("total_tokens", 0)
                                            prompt_tokens_cnt = usage_data.get("prompt_tokens", 0)
                                            completion_tokens_cnt = usage_data.get("completion_tokens", 0)
                            except Exception:
                                pass

                        if total_tokens_accumulated == 0:
                            estimated_tokens += 1 # 简单估算

                    # 如果能正常走完循环，说明没有抛出异常，视为成功，重置错误计数
                    # 使用 create_task 避免阻塞 generator 的结束
                    asyncio.create_task(CacheService.reset_channel_error(channel_id))

                except (GeneratorExit, asyncio.CancelledError):
                    logger.debug(f"[{trace_id}] 客户端断开连接 (API Key: {api_key})")

                except Exception as e:
                    has_error = True
                    error_msg = str(e)
                    logger.exception(f"[{trace_id}] 流式传输过程中发生错误: {error_msg}")

                    # --- 触发错误计数与熔断 ---
                    # 传入 user_model_name，确保从对应的 Redis 池中移除
                    await ChatService._handle_channel_error(channel_id, error_msg, model_name=user_model_name)

                    # 返回 SSE 格式的错误信息给前端
                    error_chunk = {
                        "error": {
                            "message": f"上游错误: {error_msg}",
                            "type": "server_error",
                            "code": 500
                        }
                    }
                    yield f"data: {json.dumps(error_chunk)}\n\n"
                    yield "data: [DONE]\n\n"

                finally:
                    # ### 结算逻辑 ###
                    # 仅在未出错或有token时记录
                    if not has_error:
                        final_total = total_tokens_accumulated
                        if final_total == 0 and estimated_tokens > 0:
                            final_total = estimated_tokens
                            completion_tokens_cnt = estimated_tokens # 估算全部为生成

                        if final_total > 0:
                            # 【优化】使用 asyncio.create_task 实现 Fire-and-Forget
                            # 避免在此处 await 导致客户端等待服务器写入 Redis 完毕后才能关闭连接
                            # 这消除了请求结束时的延迟感
                            
                            # [修改] 调用统一的记录任务，同时写入 Redis 和 DB
                            # 【修改】传入 backend_cost
                            asyncio.create_task(
                                record_usage_task(
                                    channel_id=channel_id,
                                    model_name=user_model_name,
                                    api_key=api_key,
                                    prompt_tokens=prompt_tokens_cnt,
                                    completion_tokens=completion_tokens_cnt,
                                    total_tokens=final_total,
                                    is_stream_log=True,
                                    backend_cost=backend_cost
                                )
                            )

            return StreamingResponse(
                stream_wrapper(adapter.chat_completion_stream(req_dict)),
                media_type="text/event-stream"
            )

        # -----------------------------------------------------------
        # 5.B 非流式处理 (Non-Streaming / JSON)
        # -----------------------------------------------------------
        try:
            response_data = await adapter.chat_completion(req_dict)

            # 请求成功，重置错误计数
            await CacheService.reset_channel_error(channel_id)

            total_tokens = 0
            prompt_tokens = 0
            completion_tokens = 0
            
            usage = response_data.get("usage")
            if usage:
                total_tokens = usage.get("total_tokens", 0)
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)

            # 使用 BackgroundTask 记录使用情况
            # [修改] 调用统一任务
            # 【修改】传入 backend_cost
            background_tasks.add_task(
                record_usage_task, 
                channel_id=channel_id, 
                model_name=user_model_name, 
                api_key=api_key, 
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                is_stream_log=False,
                backend_cost=backend_cost
            )

            return JSONResponse(content=response_data)

        except Exception as e:
            logger.error(f"[{trace_id}] 上游服务请求失败: {e}")
            # --- 触发错误计数与熔断 ---
            # 传入 user_model_name，确保从对应的 Redis 池中移除
            await ChatService._handle_channel_error(channel_id, str(e), model_name=user_model_name)

            raise ExternalServiceError(detail=f"上游服务响应错误: {str(e)}")