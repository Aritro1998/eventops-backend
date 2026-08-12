from django.test import TestCase
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model

from venues.models import Venue, Space

User = get_user_model()


class TestSpace(TestCase):
    """Space.clean() is the only validation this model has - organizers
    manage Spaces exclusively through Django admin, which calls
    full_clean() before every save, so this is the actual enforcement
    point, not just documentation. It directly drives seat generation
    for any Event attached to a Space (see EventService.build_seat_specs),
    so a silent gap here would surface as wrong seat counts, not a clean
    validation error."""

    def setUp(self):
        self.organizer = User.objects.create_user(
            username="organizer",
            email="org@test.com",
            password="StrongPass@123",
            role="ORGANIZER",
        )
        self.venue = Venue.objects.create(
            name="Test Venue",
            address="123 Main St",
            city="Kolkata",
            created_by=self.organizer,
        )

    # -------------------------
    # LABELED SEATING
    # -------------------------

    def test_labeled_space_requires_rows(self):
        space = Space(
            venue=self.venue,
            name="Screen 1",
            seating_type=Space.SEATING_LABELED,
            rows=None,
            columns=10,
            label_style=Space.LABEL_ALPHA_NUMERIC,
        )
        with self.assertRaises(ValidationError):
            space.clean()

    def test_labeled_space_requires_columns(self):
        space = Space(
            venue=self.venue,
            name="Screen 1",
            seating_type=Space.SEATING_LABELED,
            rows=10,
            columns=None,
            label_style=Space.LABEL_ALPHA_NUMERIC,
        )
        with self.assertRaises(ValidationError):
            space.clean()

    def test_labeled_space_requires_label_style(self):
        space = Space(
            venue=self.venue,
            name="Screen 1",
            seating_type=Space.SEATING_LABELED,
            rows=10,
            columns=10,
            label_style=None,
        )
        with self.assertRaises(ValidationError):
            space.clean()

    def test_labeled_space_with_all_fields_is_valid(self):
        space = Space(
            venue=self.venue,
            name="Screen 1",
            seating_type=Space.SEATING_LABELED,
            rows=10,
            columns=20,
            label_style=Space.LABEL_ALPHA_NUMERIC,
        )
        space.clean()  # should not raise
        self.assertEqual(space.total_seats, 200)

    # -------------------------
    # GENERAL ADMISSION
    # -------------------------

    def test_general_admission_requires_capacity(self):
        space = Space(
            venue=self.venue,
            name="Festival Field",
            seating_type=Space.SEATING_GENERAL,
            capacity=None,
        )
        with self.assertRaises(ValidationError):
            space.clean()

    def test_general_admission_with_capacity_is_valid(self):
        space = Space(
            venue=self.venue,
            name="Festival Field",
            seating_type=Space.SEATING_GENERAL,
            capacity=500,
        )
        space.clean()  # should not raise
        self.assertEqual(space.total_seats, 500)
