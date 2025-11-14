from django.urls import path
from .views import MyProfileView, UserProfileDetailView

urlpatterns = [
    path("me/", MyProfileView.as_view(), name="my-profile"),
    path("<int:user_id>/", UserProfileDetailView.as_view(), name="user-profile"),
]
