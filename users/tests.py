from django.test import TestCase
from django.contrib.auth import get_user_model

from rest_framework.test import APIClient


User = get_user_model()


class UserTestCase(TestCase):

    def setUp(self):

        self.client = APIClient()

        # Use strong password to pass Django validators
        self.user_data = {
            "username": "testuser",
            "email": "test@test.com",
            "password": "StrongPass@123"
        }

    # -------------------------
    # REGISTER
    # -------------------------

    def test_user_registration(self):

        response = self.client.post(
            "/api/auth/register/",
            self.user_data,
            format="json"
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(User.objects.count(), 1)

    # -------------------------
    # LOGIN (JWT)
    # -------------------------

    def test_user_login(self):

        User.objects.create_user(
            username=self.user_data["username"],
            email=self.user_data["email"],
            password=self.user_data["password"]
        )

        response = self.client.post(
            "/api/auth/token/",
            {
                "username": self.user_data["username"],
                "password": self.user_data["password"]
            },
            format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    # -------------------------
    # PROTECTED ENDPOINT
    # -------------------------

    def test_protected_endpoint_requires_auth(self):

        response = self.client.get("/api/bookings/")

        self.assertEqual(response.status_code, 401)

    def test_protected_endpoint_with_auth(self):

        user = User.objects.create_user(
            username="testuser",
            email="test@test.com",
            password="StrongPass@123"
        )

        self.client.force_authenticate(user=user)

        response = self.client.get("/api/bookings/")

        self.assertNotEqual(response.status_code, 401)