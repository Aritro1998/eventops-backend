"""
Venue -> Space is the physical-location layer that Event optionally plugs
into (see Event.venue/Event.space in events/models.py). A Venue is a real
place (e.g. a multiplex building); a Space is one bookable room/screen/
field within it — deliberately named "Space" rather than "Hall" so it also
reads correctly for an outdoor GA space like a festival field, not just an
indoor room.
"""

from django.db import models
from users.models import User


class Venue(models.Model):
    """A physical location. Does not itself carry knowledge-document/RAG
    content or seat capacity — capacity is a property of its Spaces
    (see Space.total_seats below), not stored redundantly here."""
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=50)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='venues')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['name']
        

class Space(models.Model):
    """
    One bookable room/screen/field within a Venue — e.g. a multiplex's
    "Screen 1", or an open festival field. seating_type decides which
    other fields are meaningful:
    - SEATING_LABELED: rows/columns/label_style are required, capacity is
      unused. Seats get generated in a grid with real labels (A1, B5...).
    - SEATING_GENERAL: capacity is required, rows/columns/label_style are
      unused. Seats are anonymous — just a count of tickets.
    This split (rather than always requiring all four fields) is what
    lets EventService.build_seat_specs generate correct seats for both
    ticketing styles from one model.
    """
    SEATING_LABELED = 'labeled'
    SEATING_GENERAL = 'general'
    SEATING_TYPE_CHOICES = [
        (SEATING_LABELED, 'Labeled seating'),
        (SEATING_GENERAL, 'General admission'),
    ]

    LABEL_ALPHA_NUMERIC = 'alpha_numeric'
    LABEL_NUMERIC = 'numeric'
    LABEL_STYLE_CHOICES = [
        (LABEL_ALPHA_NUMERIC, 'Letter rows, numbered seats (A1, A2...)'),
        (LABEL_NUMERIC, 'Numbered rows and seats (1-1, 1-2...)'),
    ]
    
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name='spaces')
    name = models.CharField(max_length=255)
    seating_type = models.CharField(max_length=20, choices=SEATING_TYPE_CHOICES)
    
    # Only used when seating_type == SEATING_LABELED
    rows = models.PositiveIntegerField(null=True, blank=True)
    columns = models.PositiveIntegerField(null=True, blank=True)
    label_style = models.CharField(max_length=20, choices=LABEL_STYLE_CHOICES, null=True, blank=True)
    
    # Only used when seating_type == SEATING_GENERAL
    capacity = models.PositiveIntegerField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f'{self.name} ({self.venue.name})'
    
    @property
    def total_seats(self):
        """Computed, not stored — for labeled spaces this is always
        rows * columns, so storing it separately would just create a
        second number that can silently drift out of sync if rows/columns
        are edited without updating it too."""
        if self.seating_type == self.SEATING_LABELED:
            return self.rows * self.columns
        return self.capacity

    def clean(self):
        """
        Organizers manage Spaces exclusively through Django admin (there's
        no organizer-facing API for it), and admin's ModelForm calls
        full_clean() automatically before every save — so this is the
        right place for this validation. If a non-admin write path is
        ever added, this logic would need to move to (or be duplicated
        into) a DB-level CheckConstraint instead, since clean() only runs
        when something explicitly calls full_clean()/clean().
        """
        from django.core.exceptions import ValidationError
        if self.seating_type == self.SEATING_LABELED:
            if not self.rows or not self.columns or not self.label_style:
                raise ValidationError('Labeled spaces require rows, columns, and label_style.')
        elif self.seating_type == self.SEATING_GENERAL:
            if not self.capacity:
                raise ValidationError('General admission spaces require capacity.')
            
    class Meta:
        ordering = ['venue', 'name']
        constraints = [
            models.UniqueConstraint(fields=['venue', 'name'], name='unique_space_name_per_venue'),
        ]

    