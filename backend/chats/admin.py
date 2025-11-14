from django.contrib import admin
from .models import Room, RoomMember, Message


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("id", "room_code", "name", "created_by", "created_at")
    search_fields = ("room_code", "name", "created_by__username")


@admin.register(RoomMember)
class RoomMemberAdmin(admin.ModelAdmin):
    list_display = ("id", "room", "user", "joined_at")
    search_fields = ("room__room_code", "user__username")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "room", "sender", "created_at")
    search_fields = ("room__room_code", "sender__username", "content")
