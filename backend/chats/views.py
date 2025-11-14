from django.shortcuts import render

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Room, RoomMember, Message
from .serializers import RoomSerializer, MessageSerializer

User = get_user_model()


def is_member(user: User, room: Room) -> bool:
    return RoomMember.objects.filter(user=user, room=room).exists()
    

class RoomListCreateView(APIView):
    """
    GET  /api/chats/rooms/     -> 내가 속한 방 목록
    POST /api/chats/rooms/     -> 방 생성
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        memberships = RoomMember.objects.filter(user=request.user).select_related("room")
        rooms = [m.room for m in memberships]
        serializer = RoomSerializer(rooms, many=True)
        return Response({"items": serializer.data})

    def post(self, request):
        name = request.data.get("name", "")
        room = Room.objects.create(
            created_by=request.user,
            name=name,
        )
        # 방 생성한 사람을 자동으로 멤버로 추가
        RoomMember.objects.create(room=room, user=request.user)

        serializer = RoomSerializer(room)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class JoinRoomView(APIView):
    """
    POST /api/chats/rooms/join/   { "room_code": "abcd1234" }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        room_code = request.data.get("room_code")
        if not room_code:
            return Response(
                {"detail": "room_code is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        room = get_object_or_404(Room, room_code=room_code)

        membership, created = RoomMember.objects.get_or_create(
            room=room,
            user=request.user,
        )

        serializer = RoomSerializer(room)
        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class LeaveRoomView(APIView):
    """
    POST /api/chats/rooms/<room_id>/leave/
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, room_id):
        room = get_object_or_404(Room, id=room_id)
        deleted, _ = RoomMember.objects.filter(room=room, user=request.user).delete()
        if deleted == 0:
            return Response(
                {"detail": "Not a member of this room."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class RoomMessagesView(APIView):
    """
    GET  /api/chats/rooms/<room_id>/messages/   -> 방 메시지 목록
    POST /api/chats/rooms/<room_id>/messages/  -> 메시지 보내기(저장)
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, room_id):
        room = get_object_or_404(Room, id=room_id)
        if not is_member(request.user, room):
            return Response(
                {"detail": "Not a member of this room."},
                status=status.HTTP_403_FORBIDDEN,
            )

        queryset = room.messages.select_related("sender").order_by("created_at")
        serializer = MessageSerializer(queryset, many=True)
        return Response({"items": serializer.data})

    def post(self, request, room_id):
        room = get_object_or_404(Room, id=room_id)
        if not is_member(request.user, room):
            return Response(
                {"detail": "Not a member of this room."},
                status=status.HTTP_403_FORBIDDEN,
            )

        content = request.data.get("content", "").strip()
        if not content:
            return Response(
                {"detail": "content is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        msg = Message.objects.create(
            room=room,
            sender=request.user,
            content=content,
        )
        serializer = MessageSerializer(msg)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

