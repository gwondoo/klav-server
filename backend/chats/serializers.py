from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Room, RoomMember, Message

User = get_user_model()


class RoomSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(
        source="created_by.username",
        read_only=True,
    )
    member_count = serializers.IntegerField(
        source="memberships.count",
        read_only=True,
    )

    class Meta:
        model = Room
        fields = [
            "id",
            "room_code",
            "name",
            "created_by",
            "created_by_username",
            "member_count",
            "created_at",
        ]
        read_only_fields = ["id", "room_code", "created_by", "created_at"]


class MessageSerializer(serializers.ModelSerializer):
    sender_username = serializers.CharField(
        source="sender.username",
        read_only=True,
    )

    class Meta:
        model = Message
        fields = [
            "id",
            "room",
            "sender",
            "sender_username",
            "content",
            "created_at",
        ]
        read_only_fields = ["id", "room", "sender", "created_at"]
