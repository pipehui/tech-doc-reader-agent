import { agentMeta, agentStyle } from "../../agentColors";
import type { AgentKey } from "../../types";


export function AgentBadge({ agent }: { agent: AgentKey }) {
  return (
    <span className="agent-badge" style={agentStyle(agent)}>
      <i className="agent-dot" />{agentMeta[agent].label}
    </span>
  );
}
