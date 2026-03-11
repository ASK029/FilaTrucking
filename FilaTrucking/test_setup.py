import os, sys
sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'FilaTrucking.settings')
import django
django.setup()
print('Django setup OK')
