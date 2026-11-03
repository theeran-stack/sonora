from django.core.management.base import BaseCommand
from home.seeds import seed_all


class Command(BaseCommand):
    help = "Seeds database with initial FAQ and ErrorCode records (idempotent)."

    def handle(self, *args, **options):
        result = seed_all()
        faq_info = result["faqs"]
        err_info = result["error_codes"]
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded database:\n"
                f" - FAQs: {faq_info['total']} items ({faq_info['created']} created, {faq_info['updated']} refreshed)\n"
                f" - Error Codes: {err_info['total']} items ({err_info['created']} created, {err_info['updated']} refreshed)"
            )
        )

