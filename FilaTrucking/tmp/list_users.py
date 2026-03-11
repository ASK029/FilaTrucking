from django.contrib.auth import get_user_model
User = get_user_model()
print([(u.username, u.is_superuser) for u in User.objects.all()])
