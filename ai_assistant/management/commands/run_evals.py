from django.core.management.base import BaseCommand

from users.models import User
from ai_assistant.evals.runner import run_eval_suite
from ai_assistant.evals.discovery_questions import DISCOVERY_QUESTIONS
from ai_assistant.evals.booking_questions import BOOKING_QUESTIONS


class Command(BaseCommand):
    help = "Run the discovery and booking eval suites against a real user."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--suite", choices=["discovery", "booking", "all"], default="all")

    def handle(self, *args, **options):
        user = User.objects.get(username=options["username"])

        suites = []
        if options["suite"] in ("discovery", "all"):
            suites.append(("discovery", DISCOVERY_QUESTIONS))
        if options["suite"] in ("booking", "all"):
            suites.append(("booking", BOOKING_QUESTIONS))

        for name, questions in suites:
            self.stdout.write(f"\n=== {name} evals ===")
            result = run_eval_suite(questions, user)
            for r in result["results"]:
                mark = "PASS" if r["passed"] else "FAIL"
                self.stdout.write(f"[{mark}] {r['id']}")
                for failure in r["failures"]:
                    self.stdout.write(f"    - {failure}")
            self.stdout.write(f"{name}: {result['passed']}/{result['total']} passed")