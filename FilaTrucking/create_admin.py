import os
import sys
sys.path.insert(0, 'D:\\Projects\\FilaTrucking\\FilaTrucking')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'FilaTrucking.settings')
import django
django.setup()
from django.contrib.auth import get_user_model
User = get_user_model()
try:
    u = User.objects.get(username='admin')
    u.set_password('admin123')
    u.is_superuser = True
    u.is_staff = True
    u.save()
    print('Password set for existing user')
except User.DoesNotExist:
    u = User.objects.create_user('admin', 'admin@filatrucking.com', 'admin123')
    u.is_superuser = True
    u.is_staff = True
    u.save()
    print('User created')
