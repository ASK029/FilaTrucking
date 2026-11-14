"""Brute-force protection for the login form (QA-030).

Failures are counted in Django's cache (Redis in production, so all gunicorn workers share the counters):
  * per (username, client IP): 5 failures in 15 minutes locks that pair -> the real admin is not locked out by an
    attacker coming from another address;
  * per client IP: 20 failures in 15 minutes locks the address.
A successful login clears the pair's counter. nginx adds a coarse request-rate limit on top (nginx.conf).
"""
from django.contrib.auth import views as auth_views
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.core.cache import cache
from django.dispatch import receiver

WINDOW_SECONDS = 15 * 60
MAX_FAILURES_PER_USER_AND_IP = 5
MAX_FAILURES_PER_IP = 20


def client_ip(request):
    """The address nginx saw. nginx APPENDS the real peer to X-Forwarded-For, so the right-most entry is trustworthy
    and anything the client put in front of it is ignored."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _keys(username, ip):
    return f"login-fail:pair:{(username or '').lower()[:150]}|{ip}", f"login-fail:ip:{ip}"


def _count(key):
    return cache.get(key, 0)


def _hit(key):
    if cache.add(key, 1, WINDOW_SECONDS):  # first failure starts the window
        return
    try:
        cache.incr(key)
    except ValueError:  # key expired between add() and incr()
        cache.add(key, 1, WINDOW_SECONDS)


def is_locked(username, ip):
    pair_key, ip_key = _keys(username, ip)
    return _count(pair_key) >= MAX_FAILURES_PER_USER_AND_IP or _count(ip_key) >= MAX_FAILURES_PER_IP


@receiver(user_login_failed)
def record_failure(sender, credentials=None, request=None, **kwargs):
    if request is None:
        return
    pair_key, ip_key = _keys((credentials or {}).get("username", ""), client_ip(request))
    _hit(pair_key)
    _hit(ip_key)


@receiver(user_logged_in)
def clear_failures(sender, request=None, user=None, **kwargs):
    if request is not None and user is not None:
        pair_key, _ = _keys(user.get_username(), client_ip(request))
        cache.delete(pair_key)


class ThrottledLoginView(auth_views.LoginView):
    def post(self, request, *args, **kwargs):
        if is_locked(request.POST.get("username", ""), client_ip(request)):
            form = self.get_form()
            form.add_error(None, "Too many failed sign-in attempts. Please wait 15 minutes and try again.")
            response = self.render_to_response(self.get_context_data(form=form), status=429)
            response["Retry-After"] = str(WINDOW_SECONDS)
            return response
        return super().post(request, *args, **kwargs)
