from app.agents.kimi_client import KimiClient
from app.agents.planner import PlannerAgent
from app.agents.researcher import ResearcherAgent
from app.agents.writer import WriterAgent

kimi = KimiClient()
planner = PlannerAgent(kimi)
researcher = ResearcherAgent()
writer = WriterAgent(kimi)

