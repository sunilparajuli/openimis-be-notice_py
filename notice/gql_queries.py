import graphene
from core import prefix_filterset, ExtendedConnection, filter_validity
from graphene_django import DjangoObjectType
from django.utils.translation import gettext as _
from location.schema import HealthFacilityGQLType
from location import models as location_models
from core import models as core_models
from graphql import ResolveInfo
from django.db.models import Q

from .models import Notice, NoticeAttachment


class NoticeGQLType(DjangoObjectType):
    attachment_count = graphene.Int()
    priority = graphene.String()
    class Meta:
        model = Notice
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "uuid": ["exact"],
            "title": ["icontains"],
            "description": ["icontains"],
            "priority": ["exact"],
            "is_active": ["exact"],
            "schedule_publish": ["exact"],
            "publish_start_date": ["exact", "lt", "lte", "gt", "gte"],
            "created_at": ["exact", "lt", "lte", "gt", "gte"],
            "validity_from": ["exact", "lt", "lte", "gt", "gte"],
            "validity_to": ["exact", "isnull"],
            **prefix_filterset("health_facility__", HealthFacilityGQLType._meta.filter_fields),
        }
        connection_class = ExtendedConnection 
    
    def resolve_attachment_count(self, info):
        return self.notice_attachments.filter(*filter_validity()).count()

    @classmethod
    def get_queryset(cls, queryset, info):
        """
        Default queryset filtering:
        1. Apply validity filter (validity_to__isnull=True).
        2. If health_facility is null, show to all; otherwise, filter by user's health facility (row security).
        3. Only show notices where current date is after publish_start_date (if set).
        """
        user = getattr(info.context, "user", None)
        from django.conf import settings
        from datetime import datetime
        current_date = datetime.now()
        queryset = queryset.filter(*filter_validity())
        if settings.ROW_SECURITY and user and not user.is_anonymous:
            i_user = getattr(user, "i_user", None) or getattr(user, "_u", None)
            if i_user and hasattr(i_user, 'health_facility_id') and i_user.health_facility_id:
                queryset = queryset.filter(
                    Q(health_facility_id=i_user.health_facility_id) |
                    Q(health_facility__isnull=True)
                )

        return queryset.order_by('-created_at')

class NoticeAttachmentGQLType(DjangoObjectType):
    doc = graphene.String(source='document')
    class Meta:
        model = NoticeAttachment
        interfaces = (graphene.relay.Node,)
        fields = '__all__'
        filter_fields = {
            "id": ["exact"],
            "general_type": ["exact", "icontains"],
            "type": ["exact", "icontains"],
            "title": ["exact", "icontains"],
            "date": ["exact", "lt", "lte", "gt", "gte"],
            "filename": ["exact", "icontains"],
            "mime": ["exact", "icontains"],
            "url": ["exact", "icontains"],
            **prefix_filterset("notice__", NoticeGQLType._meta.filter_fields),
        }
        connection_class = ExtendedConnection

