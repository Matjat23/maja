"""Jobs: bigger tasks that Maja works on in the background, step by step.

A job goes through four roles, like my separate agent team: a planner, a researcher, a
writer and a reviewer. If the reviewer finds problems, the writer fixes them once and the
reviewer checks again. Every step is saved in a Markdown report in the jobs/ folder.

Jobs use Claude when an API key and budget are available (with web search for the
research step), and the free local model otherwise.
"""

import re
import threading
from datetime import datetime
from pathlib import Path

from brain import BrainError

PLANNER = (
    "You are the planner. Turn the request into a short, practical plan (at most 300 words): "
    "the goal, every explicit requirement, the steps, and open questions. Do not write the "
    "deliverable itself."
)
RESEARCHER = (
    "You are the researcher. Collect the facts, options and examples the plan needs. Use the "
    "excerpts from Matty's documents when they help, and say which file they came from. "
    "Never invent sources or numbers; mark anything uncertain as uncertain."
)
WRITER = (
    "You are the writer. Using the plan and the research, write the complete deliverable Matty "
    "asked for. Make it concrete and ready to use, with headings where they help. Mark anything "
    "that still needs checking."
)
REVISER = (
    "You are the writer. A reviewer found problems with your draft. Write the complete, "
    "corrected deliverable, fixing every point in the review. Return only the deliverable."
)
REVIEWER = (
    "You are the reviewer. Check the draft against the request and the plan: missing "
    "requirements, mistakes, unsupported claims, unclear parts. Start your answer with exactly "
    "'VERDICT: GOOD' if it can be handed over as it is, or 'VERDICT: NEEDS WORK' followed by a "
    "numbered list of what must change."
)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "job"


class JobRunner:
    def __init__(self, maja, folder):
        self.maja = maja
        self.folder = Path(folder)
        self.running = False

    def uses_cloud(self):
        return self.maja.cloud is not None and self.maja.cloud.usable

    def start(self, task, on_step, on_done):
        """Run the job on a background thread. on_step(text) reports progress;
        on_done(summary) gets the final message (or the error)."""
        if self.running:
            on_done("I'm already working on a job. I'll tell you when it's done.")
            return
        self.running = True

        def work():
            try:
                on_done(self.run(task, on_step))
            except BrainError as error:
                on_done(f"The job stopped: {error}")
            finally:
                self.running = False

        threading.Thread(target=work, daemon=True).start()

    def run(self, task, on_step=lambda text: None, now=None):
        """Do the whole job and return a short summary for the chat."""
        cloud = self.uses_cloud()
        spent_before = self.maja.cloud.spent_this_month() if cloud else 0.0
        steps = []

        def ask(role_name, instructions, work_so_far, web=False):
            on_step(f"{role_name} is working...")
            messages = [
                {"role": "system", "content": f"{instructions}\n\nIt's {datetime.now():%A %d %B %Y}."},
                {"role": "user", "content": f"Matty's request:\n{task}\n\n{work_so_far}".strip()},
            ]
            if cloud:
                answer = self.maja.cloud.chat(messages, web=web)
            else:
                answer = self.maja.brain.chat(messages)
            steps.append((role_name, answer))
            return answer

        plan = ask("Planner", PLANNER, "")
        excerpts = self.maja._search_documents(task)
        documents = "\n\n".join(f"[{hit['source']}]\n{hit['text']}" for hit in excerpts)
        research = ask("Researcher", RESEARCHER,
                       f"Plan:\n{plan}\n\nExcerpts from Matty's documents:\n{documents or '(none)'}",
                       web=cloud)
        draft = ask("Writer", WRITER, f"Plan:\n{plan}\n\nResearch:\n{research}")
        review = ask("Reviewer", REVIEWER, f"Plan:\n{plan}\n\nDraft:\n{draft}")
        if not review.lstrip().upper().startswith("VERDICT: GOOD"):
            draft = ask("Writer (fixing the review)", REVISER,
                        f"Plan:\n{plan}\n\nDraft:\n{draft}\n\nReview:\n{review}")
            review = ask("Reviewer (second check)", REVIEWER, f"Plan:\n{plan}\n\nDraft:\n{draft}")
        approved = review.lstrip().upper().startswith("VERDICT: GOOD")

        path = self.save(task, steps, draft, approved, cloud, now)
        cost = ""
        if cloud:
            spent = self.maja.cloud.spent_this_month()
            cost = f"\n☁️ Done with Claude: ${spent - spent_before:.2f} (${spent:.2f} of ${self.maja.cloud.budget:.2f} this month)"
        preview = draft if len(draft) <= 700 else draft[:700].rsplit(" ", 1)[0] + " ..."
        verdict = "The reviewer approved it." if approved else \
            "The reviewer still had remarks after one round of fixes, so check the report."
        return (f"✅ Job done: {task}\n\n{preview}\n\n{verdict}\n"
                f"📄 Full report: {path}  (type 'open last job'){cost}")

    def save(self, task, steps, final, approved, cloud, now=None):
        now = now or datetime.now()
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{now:%Y-%m-%d_%H%M}_{slug(task)}.md"
        parts = [
            f"# {task.splitlines()[0][:80]}\n",
            f"- Date: {now:%Y-%m-%d %H:%M}",
            f"- Model: {'Claude (' + self.maja.cloud.model + ')' if cloud else getattr(self.maja.brain, 'model', 'local model') + ' (local)'}",
            f"- Reviewer approved: {'yes' if approved else 'no'}\n",
            "## Result\n", final, "\n## How it was made\n",
        ]
        parts += [f"### {name}\n\n{text}\n" for name, text in steps]
        path.write_text("\n".join(parts) + "\n", encoding="utf-8")
        return path

    def last_report(self):
        reports = sorted(self.folder.glob("*.md")) if self.folder.exists() else []
        return reports[-1] if reports else None
