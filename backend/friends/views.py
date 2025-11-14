from django.shortcuts import render

from django.contrib.auth import get_user_model
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from profiles.models import Profile
from .models import Follow
from .serializers import FollowUserWithProfileSerializer, FollowSerializer

User = get_user_model()


def get_or_create_profile(user: User) -> Profile:
    profile, _ = Profile.objects.get_or_create(
        user=user,
        defaults={"nickname": user.username},
    )
    return profile


class FollowView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        target_id = request.data.get("user_id")
        if not target_id:
            return Response(
                {"detail": "user_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if int(target_id) == request.user.id:
            return Response(
                {"detail": "You cannot follow yourself."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            target_user = User.objects.get(id=target_id)
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        follow, created = Follow.objects.get_or_create(
            follower=request.user,
            following=target_user,
        )

        serializer = FollowSerializer(follow)
        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request):
        target_id = request.data.get("user_id")
        if not target_id:
            return Response(
                {"detail": "user_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        deleted, _ = Follow.objects.filter(
            follower=request.user,
            following_id=target_id,
        ).delete()

        if deleted == 0:
            return Response(
                {"detail": "Follow relation does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)


class FollowingListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        relations = Follow.objects.filter(
            follower=request.user
        ).select_related("following")
        items = []
        for rel in relations:
            user = rel.following
            profile = get_or_create_profile(user)
            items.append({"user": user, "profile": profile})

        serializer = FollowUserWithProfileSerializer(items, many=True)
        return Response({"items": serializer.data})


class FollowerListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        relations = Follow.objects.filter(
            following=request.user
        ).select_related("follower")
        items = []
        for rel in relations:
            user = rel.follower
            profile = get_or_create_profile(user)
            items.append({"user": user, "profile": profile})

        serializer = FollowUserWithProfileSerializer(items, many=True)
        return Response({"items": serializer.data})
