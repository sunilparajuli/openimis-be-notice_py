import logging
from django.core.mail import send_mail
from django.utils.html import escape
from django.core.exceptions import ValidationError
from django.conf import settings
from typing import List, Optional
import requests
import os
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class NotificationProvider(ABC):
    """Abstract base class for notification providers"""
    
    @abstractmethod
    def send(self, recipients: List[str], title: str, description: str, priority: str) -> bool:
        """Send notification to recipients"""
        pass


class EmailNotificationProvider(NotificationProvider):
    """Email notification provider using Django's email backend"""
    
    def send(self, recipients: List[str], title: str, description: str, priority: str) -> bool:
        """
        Send a notice email to the specified recipients.
        
        Args:
            recipients: List of email addresses
            title: The title of the notice
            description: The description of the notice
            priority: The priority of the notice
            
        Returns:
            bool: True if successful, False otherwise
            
        Raises:
            ValidationError: If sending fails
        """
        try:
            if not recipients:
                raise ValidationError("No valid recipients provided")

            # Define the HTML email template
            html_template = """
            <!doctype html>
            <html>
              <head>
                <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
                <title>Notice: {title}</title>
              </head>
              <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
                  <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin-bottom: 20px;">
                    <h1 style="color: #2c3e50; margin: 0; font-size: 24px;">📢 Notice</h1>
                  </div>
                  
                  <div style="margin-bottom: 20px;">
                    <h2 style="color: #34495e; margin-bottom: 10px; font-size: 20px;">{title}</h2>
                    <div style="background-color: #ffffff; padding: 15px; border-left: 4px solid {priority_color}; margin-bottom: 15px;">
                      <p style="margin: 0; white-space: pre-wrap;">{description}</p>
                    </div>
                    <p style="margin: 0;">
                      <strong>Priority:</strong> 
                      <span style="background-color: {priority_color}; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px; text-transform: uppercase;">
                        {priority}
                      </span>
                    </p>
                  </div>
                  
                  <div style="border-top: 1px solid #eee; padding-top: 15px; font-size: 12px; color: #666;">
                    <p style="margin: 0;">This is an automated notification from OpenIMIS.</p>
                  </div>
                </div>
              </body>
            </html>
            """

            # Priority color mapping
            priority_colors = {
                'high': '#e74c3c',
                'medium': '#f39c12',
                'low': '#27ae60',
                'urgent': '#c0392b',
                'normal': '#3498db'
            }
            
            priority_color = priority_colors.get(priority.lower(), '#3498db')

            # Replace placeholders with sanitized values
            html_message = html_template.format(
                title=escape(title),
                description=escape(description),
                priority=escape(priority),
                priority_color=priority_color
            )

            # Prepare email content
            subject = f"Notice: {title}"
            message = f"Title: {title}\n\nDescription: {description}\n\nPriority: {priority}"

            # Send email to all recipients
            send_mail(
                subject=subject,
                message=message,
                from_email=getattr(settings, 'NOTICE_FROM_EMAIL', "no-reply@openimis.org"),
                recipient_list=recipients,
                html_message=html_message,
                fail_silently=False,
            )
            
            logger.info(f"Email sent successfully to {len(recipients)} recipients for notice: {title}")
            return True
            
        except Exception as exc:
            logger.error(f"Failed to send email: {str(exc)}")
            raise ValidationError(f"Failed to send email: {str(exc)}")


class SMSNotificationProvider(NotificationProvider):
    """
    SMS notification provider - Template implementation
    This is a template that needs to be configured with your SMS provider
    """
    
    def __init__(self):
        self.gateway_url = getattr(settings, 'SMS_GATEWAY_URL', None)
        self.api_key = getattr(settings, 'SMS_GATEWAY_API_KEY', None)
        self.sender_id = getattr(settings, 'SMS_SENDER_ID', 'OpenIMIS')
        
    def send(self, recipients: List[str], title: str, description: str, priority: str) -> bool:
        """
        Send SMS notification - Template implementation
        
        This is a template method that needs to be customized based on your SMS provider.
        Common providers include: Twilio, AWS SNS, Nexmo, etc.
        
        Args:
            recipients: List of phone numbers (should be in international format)
            title: The title of the notice
            description: The description of the notice
            priority: The priority of the notice
            
        Returns:
            bool: True if successful, False otherwise
            
        Raises:
            ValidationError: If SMS provider is not configured or sending fails
        """
        if not self.gateway_url or not self.api_key:
            logger.warning("SMS provider not configured. Please set SMS_GATEWAY_URL and SMS_GATEWAY_API_KEY in settings.")
            raise ValidationError("SMS provider not configured")
            
        try:
            # Format SMS message (keep it short due to SMS length limits)
            message = f"[{priority.upper()}] {title}: {description[:100]}{'...' if len(description) > 100 else ''}"
            
            success_count = 0
            for recipient in recipients:
                try:
                    # This is a template - customize based on your SMS provider
                    payload = {
                        "api_key": self.api_key,
                        "to": recipient,
                        "message": message,
                        "from": self.sender_id
                    }
                    
                    response = requests.post(
                        self.gateway_url, 
                        json=payload, 
                        timeout=10,
                        headers={'Content-Type': 'application/json'}
                    )
                    
                    if response.status_code == 200:
                        # Customize this check based on your provider's response format
                        response_data = response.json()
                        if response_data.get("success", False):
                            success_count += 1
                        else:
                            logger.warning(f"SMS failed for {recipient}: {response_data.get('error', 'Unknown error')}")
                    else:
                        logger.warning(f"SMS gateway returned status {response.status_code} for {recipient}")
                        
                except Exception as e:
                    logger.error(f"Failed to send SMS to {recipient}: {str(e)}")
                    continue
            
            if success_count > 0:
                logger.info(f"SMS sent successfully to {success_count}/{len(recipients)} recipients")
                return True
            else:
                raise ValidationError("Failed to send SMS to any recipient")
                
        except Exception as exc:
            logger.error(f"SMS sending failed: {str(exc)}")
            raise ValidationError(f"Failed to send SMS: {str(exc)}")


class NotificationService:
    """
    Unified notification service that can handle multiple notification types
    """
    
    def __init__(self):
        self.providers = {
            'email': EmailNotificationProvider(),
            'sms': SMSNotificationProvider(),
        }
    
    def send_notification(self, 
                         notification_type: str, 
                         recipients: List[str], 
                         title: str, 
                         description: str, 
                         priority: str = 'normal') -> bool:
        """
        Send notification using the specified provider
        
        Args:
            notification_type: 'email' or 'sms'
            recipients: List of recipient addresses (emails or phone numbers)
            title: Notice title
            description: Notice description  
            priority: Priority level
            
        Returns:
            bool: True if successful
            
        Raises:
            ValidationError: If notification type is unsupported or sending fails
        """
        if notification_type not in self.providers:
            raise ValidationError(f"Unsupported notification type: {notification_type}")
            
        provider = self.providers[notification_type]
        return provider.send(recipients, title, description, priority)
    
    def send_multi_channel(self, 
                          channels: dict, 
                          title: str, 
                          description: str, 
                          priority: str = 'normal') -> dict:
        """
        Send notifications across multiple channels
        
        Args:
            channels: Dict mapping channel type to recipient list
                     e.g., {'email': ['user@example.com'], 'sms': ['+1234567890']}
            title: Notice title
            description: Notice description
            priority: Priority level
            
        Returns:
            dict: Results for each channel
        """
        results = {}
        
        for channel_type, recipients in channels.items():
            if not recipients:
                continue
                
            try:
                success = self.send_notification(
                    channel_type, recipients, title, description, priority
                )
                results[channel_type] = {'success': success, 'error': None}
            except Exception as e:
                results[channel_type] = {'success': False, 'error': str(e)}
                logger.error(f"Failed to send {channel_type} notification: {str(e)}")
        
        return results


# Legacy function for backward compatibility
def send_notice_email(recipients: List[str], title: str, description: str, priority: str) -> None:
    """
    Legacy function - use NotificationService.send_notification instead
    """
    service = NotificationService()
    service.send_notification('email', recipients, title, description, priority)