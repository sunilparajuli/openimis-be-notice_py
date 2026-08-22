import uuid
from django.db import models
from django.conf import settings
from django.core.mail import send_mail
from django.contrib.auth.models import User
from location.models import HealthFacility 
from tasks_management.models import TaskGroup
from core import fields, TimeUtils, models as core_models

class Notice(core_models.VersionedModel):
    PRIORITY_CHOICES = (
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
    )

    uuid = models.CharField(
        max_length=36, default=uuid.uuid4, unique=True, db_column="NoticeUUID"
    )
    id = models.AutoField(primary_key=True, db_column="NoticeID")
    title = models.CharField(max_length=255, db_column="Title")
    description = models.TextField(db_column="Description")
    priority = models.CharField(max_length=6, choices=PRIORITY_CHOICES, default='MEDIUM', db_column="Priority")
    health_facility = models.ForeignKey(
        HealthFacility,
        on_delete=models.CASCADE,
        related_name='notices',
        null=True,  
        blank=True,
        db_column="HFID"
    )    
    task_group = models.ForeignKey(
        TaskGroup,
        on_delete=models.CASCADE,
        related_name='notices',
        null=True,
        blank=True,
        db_column="TaskGroupID"
    )
    created_at = fields.DateTimeField(default=TimeUtils.now, db_column="CreatedAt")
    updated_at = models.DateTimeField(auto_now=True, db_column="UpdatedAt")
    schedule_publish = models.BooleanField(default=False, db_column="SchedulePublish")  
    publish_start_date = models.DateTimeField(null=True, blank=True, db_column="PublishStartDate")  
    is_active = models.BooleanField(default=True, db_column="IsActive")

    class Meta:
        db_table = 'tblNotices'

    def __str__(self):
        target = self.health_facility or self.task_group or "All"
        return f"{self.title} ({self.priority}) - {target}"


class NoticeAttachment(core_models.VersionedModel):
    id = models.AutoField(primary_key=True, db_column="AttachmentID")
    uuid = models.CharField(
        max_length=36, default=uuid.uuid4, unique=True, db_column="AttachmentUUID"
    )
    notice = models.ForeignKey(
        Notice, on_delete=models.CASCADE, related_name='notice_attachments', db_column="NoticeID"
    )
    general_type = models.CharField(
        max_length=4,
        choices=(('FILE', 'File'), ('URL', 'URL')),
        default='FILE',
        db_column="GeneralType",
        help_text="Indicates whether this is a file attachment or a URL link."
    )
    type = models.TextField(blank=True, null=True, db_column="Type", help_text="Custom type description if needed.")
    title = models.TextField(blank=True, null=True, db_column="Title", help_text="Title or name of the attachment.")
    date = fields.DateField(blank=True, default=TimeUtils.now, db_column="Date", help_text="Date of the attachment.")
    filename = models.TextField(blank=True, null=True, db_column="Filename", help_text="Original filename of the uploaded file.")
    mime = models.TextField(blank=True, null=True, db_column="Mime", help_text="MIME type of the file (e.g., 'application/pdf').")
    module = models.TextField(blank=False, null=True, default="notice", db_column="Module", help_text="Module identifier for future core integration.")
    url = models.TextField(blank=True, null=True, db_column="URL", help_text="URL link to the attachment if general_type is 'URL'.")
    document = models.TextField(blank=True, null=True, db_column="Document", help_text="Base64-encoded file content if general_type is 'FILE'.")

    class Meta:
        db_table = 'tblNoticeAttachments'

    def __str__(self):
        return f"{self.title or self.filename or 'Unnamed'} - {self.notice.title}"


class NoticeMutation(core_models.UUIDModel, core_models.ObjectMutation):
    notice = models.ForeignKey(Notice, models.DO_NOTHING,
                               related_name='notice_mutations', db_column="NoticeID")
    mutation = models.ForeignKey(
        core_models.MutationLog, models.DO_NOTHING, related_name='notice_category', db_column="MutationLogID")

    class Meta:
        managed = True
        db_table = "notice_NoticeMutation"
