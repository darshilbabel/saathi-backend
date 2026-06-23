from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden

_LOG_VIEWER_PREFIX = '/log-viewer/'


class StaffRequiredForLogViewer:
    """Restrict /log-viewer/ to staff users; redirect unauthenticated users to admin login."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(_LOG_VIEWER_PREFIX):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), login_url='/admin/login/')
            if not request.user.is_staff:
                return HttpResponseForbidden("Access restricted to staff members.")
        return self.get_response(request)