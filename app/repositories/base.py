# app/repositories/base.py
from typing import TypeVar, Generic, Type, Optional, List, Dict, Any, Tuple, Union
from tortoise.models import Model
from tortoise.expressions import Q
from tortoise.queryset import QuerySet
import abc

# 定义泛型 T
BaseModelType = TypeVar("BaseModelType", bound=Model)

class BaseRepository(Generic[BaseModelType], metaclass=abc.ABCMeta):
    """
    模型的 Repository 基类。
    """
    model: Type[BaseModelType]

    def _get_queryset(self) -> QuerySet[BaseModelType]:
        return self.model.all()

    # --- 修正点1: ID 类型改为 Union[int, str] 或 Any，适配 IntField 主键 ---
    async def get_by_id(self, id: Union[int, str], prefetch: Optional[List[str]] = None) -> Optional[BaseModelType]:
        qs = self._get_queryset()
        if prefetch:
            qs = qs.prefetch_related(*prefetch)
        return await qs.get_or_none(pk=id)

    async def get_by(self, prefetch: Optional[List[str]] = None, **kwargs) -> Optional[BaseModelType]:
        qs = self._get_queryset()
        if prefetch:
            qs = qs.prefetch_related(*prefetch)
        return await qs.get_or_none(**kwargs)

    async def filter(
        self,
        *args: Q,
        prefetch: Optional[List[str]] = None,
        order_by: Optional[List[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        **kwargs: Any
    ) -> List[BaseModelType]:
        query = self.model.filter(*args, **kwargs)
        if prefetch:
            query = query.prefetch_related(*prefetch)
        
        if order_by:
            query = query.order_by(*order_by)
        if offset is not None:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)
            
        return await query

    async def all(self, prefetch: Optional[List[str]] = None, order_by: Optional[List[str]] = None) -> List[BaseModelType]:
        qs = self._get_queryset()
        if order_by:
            qs = qs.order_by(*order_by)
        if prefetch:
            qs = qs.prefetch_related(*prefetch)
        return await qs

    async def count(self, *args: Q, **kwargs: Any) -> int:
        return await self._get_queryset().filter(*args, **kwargs).count()

    async def create(self, **kwargs: Any) -> BaseModelType:
        return await self.model.create(**kwargs)

    async def update(self, id: Union[int, str], data: Dict[str, Any], prefetch: Optional[List[str]] = None) -> Optional[BaseModelType]:
        """
        适用于 Admin 面板的更新（触发 auto_now, signals 等）。
        不适用于高并发计数。
        """
        data.pop("id", None) # 防止修改主键
        obj = await self.get_by_id(id, prefetch=prefetch)
        
        if obj:
            for key, value in data.items():
                if hasattr(obj, key): # 增加安全性检查
                    setattr(obj, key, value)
            await obj.save()
            return obj
        return None
    
    # --- 新增: 高性能批量更新 (不触发 save 信号，但速度快) ---
    async def bulk_update_by_filter(self, update_data: Dict[str, Any], **filters) -> int:
        """
        使用 SQL update 语句直接更新，适用于不需获取对象内容的场景
        返回影响行数
        """
        return await self.model.filter(**filters).update(**update_data)

    async def delete(self, id: Union[int, str]) -> int:
        return await self._get_queryset().filter(pk=id).delete()

    async def exists(self, **kwargs) -> bool:
        return await self._get_queryset().filter(**kwargs).exists()
    
    async def update_or_create(
        self, defaults: Dict[str, Any], **kwargs: Any
    ) -> Tuple[BaseModelType, bool]:
        instance, created = await self.model.update_or_create(
            defaults=defaults, **kwargs
        )
        return instance, created