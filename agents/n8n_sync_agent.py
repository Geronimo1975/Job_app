"""
N8nSyncAgent — sincronizare cu n8n (push rezultate / pull comenzi pending).

Erorile n8n NU pică pipeline-ul: întoarce PARTIAL, pentru că Excel rămâne backup.
"""

from agents.base_agent import BaseAgent, AgentResult, AgentStatus
from sender.n8n_client import N8nClient


class N8nSyncAgent(BaseAgent):
    """Agent pentru comunicarea cu webhook-urile n8n."""

    def __init__(self):
        super().__init__("N8nSyncAgent")
        self.client = N8nClient(self.config)

    def run(self, action: str = "push", jobs: list[dict] | None = None, **kwargs) -> AgentResult:
        self._start()
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)

        if action == "push":
            result = self._push(jobs or [])
        elif action == "pending":
            result = self._pending()
        else:
            result.status = AgentStatus.FAILED
            result.message = f"Acțiune necunoscută: {action}"

        return self._finish(result)

    def _push(self, jobs: list[dict]) -> AgentResult:
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)
        if not jobs:
            result.status = AgentStatus.SUCCESS
            result.message = "Niciun job de trimis la n8n"
            return result
        try:
            out = self.client.ingest(jobs)
            upserted = out.get("upserted", 0)
            result.data = {"upserted": upserted}
            result.items_processed = len(jobs)
            result.items_succeeded = upserted
            if out.get("skipped"):
                result.status = AgentStatus.SUCCESS
                result.message = "n8n dezactivat — push omis"
            else:
                result.status = AgentStatus.SUCCESS
                result.message = f"{upserted} joburi sincronizate în Sheet via n8n"
        except Exception as e:  # rețea/HTTP — nu pică pipeline-ul
            result.status = AgentStatus.PARTIAL
            result.message = "n8n indisponibil — date păstrate doar în Excel local"
            result.errors.append(str(e))
            self.logger.warning(f"n8n push eșuat: {e}")
        return result

    def _pending(self) -> AgentResult:
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)
        try:
            commands = self.client.get_pending()
            result.data = {"commands": commands}
            result.items_processed = len(commands)
            result.items_succeeded = len(commands)
            result.status = AgentStatus.SUCCESS
            result.message = f"{len(commands)} comenzi pending de la n8n"
        except Exception as e:
            result.status = AgentStatus.PARTIAL
            result.message = "n8n indisponibil — nu pot prelua comenzi pending"
            result.errors.append(str(e))
            self.logger.warning(f"n8n pending eșuat: {e}")
        return result
