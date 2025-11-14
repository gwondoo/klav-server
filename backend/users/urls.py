from django.urls import path
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from .views import RegisterView, MeView

urlpatterns = [
    # 회원가입
    path("register/", RegisterView.as_view(), name="register"),

    # 로그인 (JWT 발급)
    path("login/", TokenObtainPairView.as_view(), name="token_obtain_pair"),

    # 토큰 재발급
    path("refresh/", TokenRefreshView.as_view(), name="token_refresh"),

    # 내 정보 조회
    path("me/", MeView.as_view(), name="me"),
]
