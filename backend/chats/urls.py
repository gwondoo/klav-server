from django.urls import path

from .views import (
    RoomListCreateView,
    JoinRoomView,
    LeaveRoomView,
    RoomMessagesView,
)

urlpatterns = [
    # 방 목록 조회 / 방 생성
    path("rooms/", RoomListCreateView.as_view(), name="room-list-create"),

    # 방 아이디(코드)로 들어가기
    path("rooms/join/", JoinRoomView.as_view(), name="room-join"),

    # 방 나가기
    path("rooms/<int:room_id>/leave/", LeaveRoomView.as_view(), name="room-leave"),

    # 방별 메시지
    path("rooms/<int:room_id>/messages/", RoomMessagesView.as_view(), name="room-messages"),
]
