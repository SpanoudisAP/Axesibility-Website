from django.db import models
from django.conf import settings


class Scan(models.Model):

    STATUS_CHOICES = [ # state of the scan
        ("pending", "Pending"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="scans",
        null=True,
        blank=True,
    )

    url = models.URLField(max_length=2048)  # website address submitted.
    scanned_at = models.DateTimeField(auto_now_add=True) # date and time record was created.
    status = models.CharField(  # state of the scan
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    results = models.JSONField(default=dict, blank=True) # store results

    def __str__(self):
        return f"{self.url} - {self.scanned_at}"