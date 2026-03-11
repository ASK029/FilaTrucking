from django.core.management.base import BaseCommand
import requests
from django.conf import settings
from shipments.models import WhatsAppGroup, WhatsAppConfig


class Command(BaseCommand):
    help = 'Fetch WhatsApp groups from sidecar and sync to database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--sidecar-url',
            type=str,
            default='http://localhost:3001',
            help='Sidecar REST API URL (default: http://localhost:3001)'
        )

    def handle(self, *args, **options):
        sidecar_url = options['sidecar_url']
        
        self.stdout.write(self.style.SUCCESS(f'Fetching groups from {sidecar_url}/api/groups...'))
        
        try:
            # Fetch groups from sidecar
            response = requests.get(f'{sidecar_url}/api/groups', timeout=5)
            response.raise_for_status()
            
            data = response.json()
            groups = data.get('groups', [])
            
            if not groups:
                self.stdout.write(self.style.WARNING('No groups returned from sidecar'))
                return
            
            # Sync groups to database
            synced_count = 0
            for group_data in groups:
                group_jid = group_data.get('jid')
                group_name = group_data.get('name', 'Unknown Group')
                participants = group_data.get('participants', 0)
                
                if group_jid:
                    group, created = WhatsAppGroup.objects.update_or_create(
                        group_jid=group_jid,
                        defaults={
                            'group_name': group_name,
                            'participant_count': participants,
                        }
                    )
                    synced_count += 1
                    status = "✓ Created" if created else "✓ Updated"
                    self.stdout.write(
                        self.style.SUCCESS(f'{status}: {group_name} ({participants} members)')
                    )
            
            self.stdout.write(
                self.style.SUCCESS(f'\n✅ Successfully synced {synced_count} groups')
            )
            
        except requests.exceptions.ConnectionError:
            self.stdout.write(
                self.style.ERROR(
                    f'❌ Could not connect to sidecar at {sidecar_url}\n'
                    f'Make sure the sidecar container is running.\n'
                    f'Command: docker-compose up'
                )
            )
        except requests.exceptions.Timeout:
            self.stdout.write(
                self.style.ERROR(f'❌ Sidecar request timed out')
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Error: {str(e)}')
            )
