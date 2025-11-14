from django.urls import path
from .views import FollowView, FollowingListView, FollowerListView

urlpatterns = [
    path("follow/", FollowView.as_view(), name="follow"),
    path("following/", FollowingListView.as_view(), name="following-list"),
    path("followers/", FollowerListView.as_view(), name="follower-list"),
]
