from django.urls import path
from .views import EventViewSet
from rest_framework.routers import DefaultRouter

app_name = "events"

router = DefaultRouter()
router.register("", EventViewSet, basename="events")
urlpatterns = router.urls
