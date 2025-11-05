import logging
import graphene
from .models import Notice, NoticeAttachment
from .services import NotificationService  # Import the new service
from core.schema import OpenIMISMutation
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils.translation import gettext as _
from location.models import HealthFacility
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
        if hasattr(notice.health_facility, 'email') and notice.health_facility.email:
            email_recipients.append(notice.health_facility.email)
        
        # Get SMS recipients (customize based on your model structure)
        sms_recipients = []
        if hasattr(notice.health_facility, 'phone') and notice.health_facility.phone:
            sms_recipients.append(notice.health_facility.phone)
        
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
        schedule_publish = Boolean(required=False)
        publish_start_date = Date(required=False)
        attachments = List(NoticeAttachmentInput, required=False)
        auto_send_notification = Boolean(required=False)  # New field to control auto-sending
        notification_types = List(String, required=False)  # New field to specify notification types
        use_async = Boolean(required=False)  # New field to control async/sync execution

    @classmethod
    def async_mutate(cls, user, **data):
        try:
            if isinstance(user, AnonymousUser) or not user.id:
                raise ValidationError("Authentication required")
            if not user.has_perms(["notice.add_notice"]):
                raise PermissionDenied("Unauthorized")
                
            health_facility = None
            if data.get('health_facility_id'):
                health_facility = HealthFacility.objects.get(id=data.get("health_facility_id"))
            
            notice = Notice(
                title=data["title"],
                description=data["description"],
                priority=data["priority"],
                health_facility=health_facility,
                schedule_publish=data.get("schedule_publish", False),
                publish_start_date=data.get("publish_start_date"),
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


class UpdateNoticeMutation(OpenIMISMutation):
    _mutation_module = "notice"
    _mutation_class = "UpdateNoticeMutation"

    class Input(OpenIMISMutation.Input):
        client_mutation_id = String(required=False)
        client_mutation_label = String(required=False)
        uuid = String(required=True)
        title = String(required=True)
        description = String(required=True)
        priority = String(required=True)
        health_facility_id = Int(required=False, source='healthFacilityId')
        schedule_publish = Boolean(required=False)
        publish_start_date = Date(required=False)
        auto_send_notification = Boolean(required=False)  
        notification_types = List(String, required=False) 
        use_async = Boolean(required=False)  

    @classmethod
    def async_mutate(cls, user, **data):
        try:
            if isinstance(user, AnonymousUser) or not user.id:
                raise ValidationError("Authentication required")
            if not user.has_perms(["notice.change_notice"]):
                raise PermissionDenied("Unauthorized")

            notice = Notice.objects.get(uuid=data["uuid"], is_active=True)
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
                notice.health_facility = HealthFacility.objects.get(id=data["health_facility_id"])
                
            notice.save()
            return None  # Success, no errors
            
        except Notice.DoesNotExist:
            return [{"message": "Notice not found", "detail": str(data["uuid"])}]
        except Exception as exc:
            logger.error(f"Failed to update notice: {str(exc)}")
            return [{"message": "Failed to update notice", "detail": str(exc)}]


class DeleteNoticeMutation(OpenIMISMutation):
    _mutation_module = "notice"
    _mutation_class = "DeleteNoticeMutation"

    class Input(OpenIMISMutation.Input):
        uuids = graphene.List(graphene.UUID, required=True)

    @classmethod
    def async_mutate(cls, user, **data):
        try:
            if isinstance(user, AnonymousUser) or not user.id:
                raise ValidationError("Authentication required")
            if not user.has_perms(["notice.delete_notice"]):
                raise PermissionDenied("Unauthorized")

            errors = []
            for uuid in data["uuids"]:
                try:
                    notice = Notice.objects.get(uuid=uuid, is_active=True)
                    notice.is_active = False  # Soft delete
                    notice.save()
                except Notice.DoesNotExist:
                    errors.append({"message": "Notice not found", "detail": str(uuid)})
            
            return errors if errors else None
            
        except Exception as exc:
            logger.error(f"Failed to delete notices: {str(exc)}")
            return [{"message": "Failed to delete notices", "detail": str(exc)}]


class ToggleNoticeStatusMutation(OpenIMISMutation):
    _mutation_module = "notice"
    _mutation_class = "ToggleNoticeStatusMutation"

    class Input(OpenIMISMutation.Input):
        uuid = graphene.UUID(required=True)
        is_active = graphene.Boolean(required=True)

    @classmethod
    def async_mutate(cls, user, **data):
        try:
            if isinstance(user, AnonymousUser) or not user.id:
                raise ValidationError("Authentication required")
            if not user.has_perms(["notice.change_notice"]):
                raise PermissionDenied("Unauthorized")

            notice = Notice.objects.get(uuid=data["uuid"])
            notice.is_active = data["is_active"]
            notice.save()
            return None
            
        except Notice.DoesNotExist:
            return [{"message": "Notice not found", "detail": str(data["uuid"])}]
        except Exception as exc:
            logger.error(f"Failed to toggle notice status: {str(exc)}")
            return [{"message": "Failed to toggle notice status", "detail": str(exc)}]


class SendNoticeNotificationMutation(OpenIMISMutation):
    """
    Unified mutation to send notifications (replaces separate email/SMS mutations)
    """
    _mutation_module = "notice"
    _mutation_class = "SendNoticeNotificationMutation"

    class Input(OpenIMISMutation.Input):
        uuid = graphene.UUID(required=True)
        notification_types = List(String, required=False)  # ['email', 'sms'] or None for all
        recipients = List(String, required=False)  # Optional custom recipient list
        use_async = Boolean(required=False)  # Control async/sync execution

    @classmethod
    def async_mutate(cls, user, **data):
        try:
            if isinstance(user, AnonymousUser) or not user.id:
                raise ValidationError("Authentication required")
            if not user.has_perms(["notice.send_notification"]):  # Updated permission
                raise PermissionDenied("Unauthorized")

            notice = Notice.objects.get(uuid=data["uuid"])
            notification_types = data.get("notification_types", ["email"])
            custom_recipients = data.get("recipients")
            use_async = data.get("use_async", True)  # Default to async if available
            
            # If custom recipients are provided, handle them directly
            if custom_recipients:
                notification_service = NotificationService()
                channels = {}
                
                for notification_type in notification_types:
                    channels[notification_type] = custom_recipients
                
                # Send notifications directly (synchronously) when using custom recipients
                results = notification_service.send_multi_channel(
                    channels=channels,
                    title=notice.title,
                    description=notice.description,
                    priority=notice.priority
                )
                
                # Check if any failed
                failed_channels = [ch for ch, result in results.items() if not result['success']]
                if failed_channels:
                    error_details = "; ".join([f"{ch}: {results[ch]['error']}" for ch in failed_channels])
                    return [{"message": f"Failed to send to some channels", "detail": error_details}]
                
            else:
                # Use standard recipients from health facility
                success, result = execute_notification_task(
                    notice.id, 
                    notification_types, 
                    use_async
                )
                
                if not success:
                    return [{"message": "Failed to send notification", "detail": result.get('error', 'Unknown error')}]
            
            return None  # Success
            
        except Notice.DoesNotExist:
            return [{"message": "Notice not found", "detail": str(data["uuid"])}]
        except Exception as exc:
            logger.error(f"Failed to send notification: {str(exc)}")
            return [{"message": "Failed to send notification", "detail": str(exc)}]


# Legacy mutations for backward compatibility
class SendNoticeEmailMutation(OpenIMISMutation):
    """Legacy mutation - use SendNoticeNotificationMutation instead"""
    _mutation_module = "notice"
    _mutation_class = "SendNoticeEmailMutation"

    class Input(OpenIMISMutation.Input):
        uuid = graphene.UUID(required=True)

    @classmethod
    def async_mutate(cls, user, **data):
        # Delegate to the new unified mutation
        return SendNoticeNotificationMutation.async_mutate(
            user, 
            uuid=data["uuid"], 
            notification_types=["email"]
        )


class SendNoticeSMSMutation(OpenIMISMutation):
    """Legacy mutation - use SendNoticeNotificationMutation instead"""  
    _mutation_module = "notice"
    _mutation_class = "SendNoticeSMSMutation"

    class Input(OpenIMISMutation.Input):
        uuid = graphene.UUID(required=True)

    @classmethod
    def async_mutate(cls, user, **data):
        # Delegate to the new unified mutation
        return SendNoticeNotificationMutation.async_mutate(
            user, 
            uuid=data["uuid"], 
            notification_types=["sms"]
        )


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
            attachment.general_type = data["general_type"]
            attachment.type = data.get("type")
            attachment.title = data.get("title")
            attachment.date = data.get("date")
            attachment.filename = data.get("filename")
            attachment.mime = data.get("mime")
            attachment.url = data.get("url")
            attachment.document = data.get("document")
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
            if "client_mutation_id" in data:
                data.pop('client_mutation_id')
            if "client_mutation_label" in data:
                data.pop('client_mutation_label')
            
            attachment = NoticeAttachment.objects.get(id=data["id"])
            attachment.delete()
            return None  # Success, no errors
            
        except Exception as exc:
            logger.error(f"Failed to delete notice attachment: {str(exc)}")
            return [{
                "message": "Failed to delete notice attachment",
                "detail": str(exc)
            }]