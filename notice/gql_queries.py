import graphene
from core import prefix_filterset, ExtendedConnection,filter_validity
from graphene_django import DjangoObjectType
from django.utils.translation import gettext as _
import graphene
from graphene_django import DjangoObjectType
from location.schema import HealthFacilityGQLType
from location import models as location_models
from core import models as core_models
from graphql import ResolveInfo
from django.db.models import Q  # Add this import at the top for Q objects

from .models import Notice, NoticeAttachment


class NoticePriority(graphene.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    @property
    def description(self):
        if self == NoticePriority.LOW:
            return _("Low priority")
        elif self == NoticePriority.MEDIUM:
            return _("Medium priority")
        elif self == NoticePriority.HIGH:
            return _("High priority")
        return ""

class NoticeGQLType(DjangoObjectType):
    attachment_count = graphene.Int()
    priority = NoticePriority()
    class Meta:
        model = Notice
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "uuid": ["exact"],
            "id": ["exact"],
            "title": ["icontains"],
            "description": ["icontains"],
            "priority": ["exact"],
            "created_at": ["exact", "lt", "lte", "gt", "gte"],
            "is_active" : ["exact"],
            **prefix_filterset("health_facility__", HealthFacilityGQLType._meta.filter_fields),

        }
        connection_class = ExtendedConnection 
    
    def resolve_attachment_count(self, info):
    # Count the number of attachments related to this notice
        return self.attachments.count()

    @classmethod
    def get_queryset(cls, queryset, info):
        """
        Default queryset filtering:
        1. Apply validity filter (validity_to__isnull=True).
        2. If health_facility is null, show to all; otherwise, filter by user's health facility (row security).
        3. Only show notices where current date is after publish_start_date (if set).
        """
        user = info.context.user
        from django.conf import settings
        from datetime import datetime
        user = info.context.user
        current_date = datetime.now()
        if settings.ROW_SECURITY:
            # TechnicalUsers don't have health_facility_id attribute
            if hasattr(user._u, 'health_facility_id') and user._u.health_facility_id:
                # Filter notices where health_facility matches user's HF or is null
                queryset = queryset.filter(
                    Q(health_facility_id=user._u.health_facility_id) |
                    Q(health_facility__isnull=True) |
                    Q(publish_start_date__isnull=True) |
                    Q(publish_start_date__gte=current_date)
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

