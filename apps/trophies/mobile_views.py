"""iOS Trophy endpoints.

GET /api/trophies/me/  →  full catalogue with per-trophy earned + progress
"""
from django.views.decorators.csrf import csrf_exempt
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .services import list_trophies_for


@csrf_exempt
@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def trophies_for_me(request):
    """Returns the full trophy catalogue with the current user's
    earned + progress state for each. iOS uses this to render the
    Profile → Trophies collection view (locked + unlocked grids)."""
    return Response({"trophies": list_trophies_for(request.user)})
