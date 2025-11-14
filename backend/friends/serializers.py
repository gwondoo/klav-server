from django.contrib.auth import get_user_model
from rest_framework import serializers

from profiles.models import Profile
from profiles.serializers import ProfileSerializer
from .models import Follow

User = get_user_model()


class SimpleUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username"]


class FollowUserWithProfileSerializer(serializers.Serializer):
    user = SimpleUserSerializer()
    profile = ProfileSerializer()


class FollowSerializer(serializers.ModelSerializer):
    class Meta:
        model = Follow
        fields = ["id", "follower", "following", "created_at"]
        read_only_fields = ["id", "created_at", "follower"]
