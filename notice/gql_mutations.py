import logging
import graphene
from .models import Notice, NoticeAttachment
from .services import NotificationService  # Import the new service
from .apps import NoticeConfig
from core.schema import OpenIMISMutation
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils.translation import gettext as _
from location.models import HealthFacility
from tasks_management.models import TaskGroup, TaskExecutor
from graphene import String, Int, Boolean, Date, List, InputObjectType
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Check if Celery is available and configured
try:
    from celery import shared_task
    from django.conf import settings
    
    # Check if Celery is properly configured
    CELERY_AVAILABLE = hasattr(settings, 'CELERY_BROKER_URL') or hasattr(settings, 'BROKER_URL')
    if CELERY_AVAILABLE:
        logger.info("Celery is available and configured - using async task execution")
    else:
        logger.info("Celery is installed but not configured - using synchronous execution")
        CELERY_AVAILABLE = False
        
except ImportError:
    logger.info("Celery is not available - using synchronous execution")
    CELERY_AVAILABLE = False
    
    # Create a dummy decorator for when Celery is not available
    def shared_task(func):
        return func


def _send_notice_notification_sync(notice_id, notification_types=None):
    """
    Synchronous function to send notice notifications
    Used when Celery is not available or for immediate execution
    
    Args:
        notice_id: ID of the notice to send
        notification_types: List of notification types ('email', 'sms') or None for all configured
        
    Returns:
        dict: Results of the notification sending
    """
    try:
        notice = Notice.objects.get(id=notice_id)
        notification_service = NotificationService()
        
        # Determine recipients and notification channels
        channels = {}
        
        # Get email recipients
        email_recipients = []
        if notice.health_facility and hasattr(notice.health_facility, 'email') and notice.health_facility.email:
            email_recipients.append(notice.health_facility.email)
        if notice.task_group:
            executors = TaskExecutor.objects.filter(task_group=notice.task_group, is_deleted=False).select_related('user', 'user__i_user')
            for ex in executors:
                user_email = getattr(ex.user, 'email', None) or getattr(getattr(ex.user, 'i_user', None), 'email', None)
                if user_email and user_email not in email_recipients:
                    email_recipients.append(user_email)
        
        # Get SMS recipients
        sms_recipients = []
        if notice.health_facility and hasattr(notice.health_facility, 'phone') and notice.health_facility.phone:
            sms_recipients.append(notice.health_facility.phone)
        if notice.task_group:
            executors = TaskExecutor.objects.filter(task_group=notice.task_group, is_deleted=False).select_related('user__i_user')
            for ex in executors:
                user_phone = getattr(getattr(ex.user, 'i_user', None), 'phone', None)
                if user_phone and user_phone not in sms_recipients:
                    sms_recipients.append(user_phone)
        
        # Build channels dict based on what's requested and available
        if not notification_types:
            notification_types = ['email']  # Default to email only
            
        if 'email' in notification_types and email_recipients:
            channels['email'] = email_recipients
            
        if 'sms' in notification_types and sms_recipients:
            channels['sms'] = sms_recipients
        
        # Send notifications
        if channels:
            results = notification_service.send_multi_channel(
                channels=channels,
                title=notice.title,
                description=notice.description,
                priority=notice.priority
            )
            
            # Log results
            for channel, result in results.items():
                if result['success']:
                    logger.info(f"Notice {notice_id} sent successfully via {channel}")
                else:
                    logger.error(f"Failed to send notice {notice_id} via {channel}: {result['error']}")
                    
            return results
        else:
            logger.warning(f"No valid recipients found for notice {notice_id}")
            return {}
            
    except Exception as exc:
        logger.error(f"Failed to send notice notification {notice_id}: {str(exc)}")
        return {'error': str(exc)}


@shared_task
def send_notice_notification(notice_id, notification_types=None):
    """
    Celery task to send notice notifications (or direct execution if Celery unavailable)
    
    Args:
        notice_id: ID of the notice to send
        notification_types: List of notification types ('email', 'sms') or None for all configured
    """
    return _send_notice_notification_sync(notice_id, notification_types)


def execute_notification_task(notice_id, notification_types=None, use_async=True):
    """
    Execute notification task - async with Celery if available, otherwise synchronous
    
    Args:
        notice_id: ID of the notice to send
        notification_types: List of notification types
        use_async: Whether to use async execution (ignored if Celery unavailable)
        
    Returns:
        tuple: (success: bool, result: dict or None)
    """
    try:
        if CELERY_AVAILABLE and use_async:
            # Execute asynchronously with Celery
            task_result = send_notice_notification.delay(notice_id, notification_types)
            logger.info(f"Notice notification task queued with ID: {task_result.id}")
            return True, {'task_id': task_result.id, 'async': True}
        else:
            # Execute synchronously
            result = _send_notice_notification_sync(notice_id, notification_types)
            if 'error' in result:
                return False, result
            else:
                return True, {'result': result, 'async': False}
                
    except Exception as exc:
        logger.error(f"Failed to execute notification task: {str(exc)}")
        return False, {'error': str(exc)}


class NoticeAttachmentInput(InputObjectType):
    general_type = String(required=False)
    type = String(required=False)
    title = String(required=False)
    date = Date(required=False)
    filename = String(required=False)
    mime = String(required=False)
    url = String(required=False)
    document = String(required=False)


class CreateNoticeMutation(OpenIMISMutation):
    _mutation_module = "notice"
    _mutation_class = "CreateNoticeMutation"

    class Input(OpenIMISMutation.Input):
        client_mutation_id = String(required=False)
        client_mutation_label = String(required=False)
        title = String(required=True)
        description = String(required=True)
        priority = String(required=True)
        health_facility_id = Int(required=False, source='healthFacilityId')
        task_group_id = Int(required=False, source='taskGroupId')
        schedule_publish = Boolean(required=False)
        publish_start_date = Date(required=False)
        created_at = Date(required=False, source='createdAt')
        attachments = List(NoticeAttachmentInput, required=False)
        auto_send_notification = Boolean(required=False)  # New field to control auto-sending
        notification_types = List(String, required=False)  # New field to specify notification types
        use_async = Boolean(required=False)  # New field to control async/sync execution

    @classmethod
    def async_mutate(cls, user, **data):
        try:
            if isinstance(user, AnonymousUser) or not user.id:
                raise ValidationError("Authentication required")
            if not user.has_perms(NoticeConfig.gql_mutation_create_notices_perms):
                raise PermissionDenied("Unauthorized")
                
            health_facility = None
            if data.get('health_facility_id'):
                health_facility = HealthFacility.objects.get(id=data.get("health_facility_id"))

            task_group = None
            if data.get('task_group_id'):
                task_group = TaskGroup.objects.get(id=data.get("task_group_id"))

            if not health_facility and not task_group:
                raise ValidationError("Either Health Facility or Task Group is required")
            
            import uuid
            created_at = data.get("created_at") or data.get("createdAt") or datetime.now()
            notice = Notice(
                uuid=uuid.uuid4(),
                title=data["title"],
                description=data["description"],
                priority=data["priority"],
                health_facility=health_facility,
                task_group=task_group,
                schedule_publish=data.get("schedule_publish", False),
                publish_start_date=data.get("publish_start_date"),
                created_at=created_at,
                is_active=False
            )
            notice.save()
            
            # Handle attachments
            attachments_data = data.get("attachments", [])
            for attachment_data in attachments_data:
                if not user.has_perms(["notice.add_notice_attachment"]):
                    raise PermissionDenied("Unauthorized to add attachments")
                    
                attachment = NoticeAttachment(
                    notice=notice,
                    general_type=attachment_data.get("general_type", "document"),
                    type=attachment_data.get("type"),
                    title=attachment_data.get("title"),
                    date=attachment_data.get("date", datetime.now().date()),
                    filename=attachment_data.get("filename"),
                    mime=attachment_data.get("mime"),
                    url=attachment_data.get("url"),
                    document=attachment_data.get("document"),
                )
                attachment.save()
            
            # Send notification if requested
            if data.get("auto_send_notification", True):  # Default to True for backward compatibility
                notification_types = data.get("notification_types", ["email"])
                use_async = data.get("use_async", True)  # Default to async if available
                
                success, result = execute_notification_task(
                    notice.id, 
                    notification_types, 
                    use_async
                )
                
                if not success:
                    logger.warning(f"Notification sending failed: {result}")
                    # Don't return error here as notice was created successfully
                    # Just log the notification failure
                
            return None  # Success, no errors
            
        except Exception as exc:
            logger.error(f"Failed to create notice: {str(exc)}")
            return [{
                "message": "Failed to create notice or attachments",
                "detail": str(exc)
            }]


from graphql_relay import from_global_id

def _get_notice_by_uuid_or_id(uuid_or_id):
    if not uuid_or_id:
        return None
    # 1. Try UUID string
    try:
        return Notice.objects.get(uuid=uuid_or_id, validity_to__isnull=True)
    except Exception:
        pass
    # 2. Try Relay global id
    try:
        _type, _id = from_global_id(str(uuid_or_id))
        if _id:
            return Notice.objects.get(id=int(_id), validity_to__isnull=True)
    except Exception:
        pass
    # 3. Try integer ID
    try:
        return Notice.objects.get(id=int(uuid_or_id), validity_to__isnull=True)
    except Exception:
        pass
    return None

class UpdateNoticeMutation(OpenIMISMutation):
    _mutation_module = "notice"
    _mutation_class = "UpdateNoticeMutation"

    class Input(OpenIMISMutation.Input):
        client_mutation_id = String(required=False)
        client_mutation_label = String(required=False)
        uuid = String(required=False)
        id = String(required=False)
        title = String(required=True)
        description = String(required=True)
        priority = String(required=True)
        health_facility_id = Int(required=False, source='healthFacilityId')
        task_group_id = Int(required=False, source='taskGroupId')
        schedule_publish = Boolean(required=False)
        publish_start_date = Date(required=False)
        created_at = Date(required=False, source='createdAt')
        auto_send_notification = Boolean(required=False)  
        notification_types = List(String, required=False) 
        use_async = Boolean(required=False)  

    @classmethod
    def async_mutate(cls, user, **data):
        try:
            if isinstance(user, AnonymousUser) or not user.id:
                raise ValidationError("Authentication required")
            if not user.has_perms(NoticeConfig.gql_mutation_update_notices_perms):
                raise PermissionDenied("Unauthorized")

            target_id = data.get("uuid") or data.get("id")
            notice = _get_notice_by_uuid_or_id(target_id)
            if not notice:
                raise Notice.DoesNotExist(f"Notice with identifier '{target_id}' not found")

            notice.save_history()
            if "client_mutation_id" in data:
                data.pop('client_mutation_id')
            if "client_mutation_label" in data:
                data.pop('client_mutation_label')            
            if "title" in data:
                notice.title = data["title"]
            if "description" in data:
                notice.description = data["description"]
            if "priority" in data:
                notice.priority = data["priority"]
            if "health_facility_id" in data:
                if data["health_facility_id"]:
                    notice.health_facility = HealthFacility.objects.get(id=data["health_facility_id"])
                else:
                    notice.health_facility = None
            if "task_group_id" in data:
                if data["task_group_id"]:
                    notice.task_group = TaskGroup.objects.get(id=data["task_group_id"])
                else:
                    notice.task_group = None

            if not notice.health_facility and not notice.task_group:
                raise ValidationError("Either Health Facility or Task Group is required")

            if "schedule_publish" in data:
                notice.schedule_publish = data["schedule_publish"]
            if "publish_start_date" in data:
                notice.publish_start_date = data["publish_start_date"]
            if "created_at" in data and data["created_at"]:
                notice.created_at = data["created_at"]
            elif "createdAt" in data and data["createdAt"]:
                notice.created_at = data["createdAt"]
                
            notice.save()
            return None  # Success, no errors
            
        except Notice.DoesNotExist as dne:
            return [{"message": "Notice not found", "detail": str(dne)}]
        except Exception as exc:
            logger.error(f"Failed to update notice: {str(exc)}")
            return [{"message": "Failed to update notice", "detail": str(exc)}]


class DeleteNoticeMutation(OpenIMISMutation):
    _mutation_module = "notice"
    _mutation_class = "DeleteNoticeMutation"

    class Input(OpenIMISMutation.Input):
        uuids = graphene.List(graphene.String, required=True)

    @classmethod
    def async_mutate(cls, user, **data):
        try:
            if isinstance(user, AnonymousUser) or not user.id:
                raise ValidationError("Authentication required")
            if not user.has_perms(NoticeConfig.gql_mutation_delete_notices_perms):
                raise PermissionDenied("Unauthorized")

            errors = []
            for uuid_or_id in data["uuids"]:
                notice = _get_notice_by_uuid_or_id(uuid_or_id)
                if notice:
                    notice.delete_history()
                else:
                    errors.append({"message": "Notice not found", "detail": str(uuid_or_id)})
            
            return errors if errors else None
            
        except Exception as exc:
            logger.error(f"Failed to delete notices: {str(exc)}")
            return [{"message": "Failed to delete notices", "detail": str(exc)}]


class ToggleNoticeStatusMutation(graphene.Mutation):
    """Simple mutation that bypasses OpenIMIS mutation log - no NoticeMutation table needed."""
    success = graphene.Boolean()
    error = graphene.String()

    class Arguments:
        uuid = graphene.UUID(required=True)
        is_active = graphene.Boolean(required=True)

    @classmethod
    def mutate(cls, root, info, uuid, is_active):
        user = info.context.user
        try:
            if isinstance(user, AnonymousUser) or not user.id:
                raise ValidationError("Authentication required")
            if not user.has_perms(NoticeConfig.gql_mutation_toggle_notice_status_perms):
                raise PermissionDenied("Unauthorized")

            notice = Notice.objects.get(uuid=uuid)
            notice.is_active = is_active
            notice.save()
            return ToggleNoticeStatusMutation(success=True, error=None)

        except Notice.DoesNotExist:
            return ToggleNoticeStatusMutation(success=False, error=f"Notice not found: {uuid}")
        except Exception as exc:
            logger.error(f"Failed to toggle notice status: {str(exc)}")
            return ToggleNoticeStatusMutation(success=False, error=str(exc))


class CreateNoticeAttachmentMutation(OpenIMISMutation):
    _mutation_module = "notice"
    _mutation_class = "CreateNoticeAttachmentMutation"

    class Input(OpenIMISMutation.Input):
        notice_uuid = graphene.String(required=True)
        general_type = graphene.String(required=False)
        type = graphene.String()
        title = graphene.String()
        date = graphene.Date()
        filename = graphene.String()
        mime = graphene.String()
        url = graphene.String()
        document = graphene.String()

    @classmethod
    def async_mutate(cls, user, **data):
        try:
            if isinstance(user, AnonymousUser) or not user.id:
                raise ValidationError("Authentication required")
            if not user.has_perms(["notice.add_notice_attachment"]):
                raise PermissionDenied("Unauthorized")
                
            notice = Notice.objects.get(uuid=data["notice_uuid"])
            
            client_mutation_id = data.get("client_mutation_id")
            if client_mutation_id:
                data.pop('client_mutation_id')
            if "client_mutation_label" in data:
                data.pop('client_mutation_label')

            attachment = NoticeAttachment(
                notice=notice,
                general_type=data.get("general_type", "document"),
                type=data.get("type"),
                title=data.get("title"),
                date=data.get("date", datetime.now().date()),
                filename=data.get("filename"),
                mime=data.get("mime"),
                url=data.get("url"),
                document=data.get("document"),
            )
            
            if client_mutation_id:
                import uuid
                mutation_uuid = uuid.UUID(client_mutation_id)
                attachment.save()
            else:
                attachment.save()
                
            return None  # Success, no errors
            
        except Exception as exc:
            logger.error(f"Failed to create notice attachment: {str(exc)}")
            return [{
                "message": "Failed to create notice attachment",
                "detail": str(exc)
            }]


class UpdateNoticeAttachmentMutation(OpenIMISMutation):
    _mutation_module = "notice"
    _mutation_class = "UpdateNoticeAttachmentMutation"

    class Input(OpenIMISMutation.Input):
        uuid = graphene.String(required=True)
        general_type = graphene.String(required=True)
        type = graphene.String()
        title = graphene.String()
        date = graphene.Date()
        filename = graphene.String()
        mime = graphene.String()
        url = graphene.String()
        document = graphene.String()

    @classmethod
    def async_mutate(cls, user, **data):
        try:
            if isinstance(user, AnonymousUser) or not user.id:
                raise ValidationError("Authentication required")
            if not user.has_perms(["notice.change_notice_attachment"]):
                raise PermissionDenied("Unauthorized")

            attachment = NoticeAttachment.objects.get(uuid=data["uuid"])
            
            client_mutation_id = data.get("client_mutation_id")
            if client_mutation_id:
                data.pop('client_mutation_id')
            if "client_mutation_label" in data:
                data.pop('client_mutation_label')
                
            attachment.general_type = data["general_type"]
            attachment.type = data.get("type")
            attachment.title = data.get("title")
            attachment.date = data.get("date")
            attachment.filename = data.get("filename")
            attachment.mime = data.get("mime")
            attachment.url = data.get("url")
            attachment.document = data.get("document")
            
            if client_mutation_id:
                import uuid
                mutation_uuid = uuid.UUID(client_mutation_id)
                attachment.save()
            else:
                attachment.save()
                
            return None  # Success, no errors
            
        except Exception as exc:
            logger.error(f"Failed to update notice attachment: {str(exc)}")
            return [{
                "message": "Failed to update notice attachment",
                "detail": str(exc)
            }]


class DeleteNoticeAttachmentMutation(OpenIMISMutation):
    _mutation_module = "notice"
    _mutation_class = "DeleteNoticeAttachmentMutation"

    class Input(OpenIMISMutation.Input):
        id = graphene.String(required=True)

    @classmethod
    def async_mutate(cls, user, **data):
        try:
            if isinstance(user, AnonymousUser) or not user.id:
                raise ValidationError("Authentication required")
            if not user.has_perms(["notice.delete_notice_attachment"]):
                raise PermissionDenied("Unauthorized")
                
            # Clean up client mutation fields
            client_mutation_id = data.get("client_mutation_id")
            if client_mutation_id:
                data.pop('client_mutation_id')
            if "client_mutation_label" in data:
                data.pop('client_mutation_label')
            
            attachment = NoticeAttachment.objects.get(id=data["id"])
            if client_mutation_id:
                import uuid
                mutation_uuid = uuid.UUID(client_mutation_id)
                attachment.delete()
            else:
                attachment.delete()
                
            return None  # Success, no errors
            
        except Exception as exc:
            logger.error(f"Failed to delete notice attachment: {str(exc)}")
            return [{
                "message": "Failed to delete notice attachment",
                "detail": str(exc)
            }]