import type { ExecutionContractMetadata } from "../types";

export const CURRENT_EXECUTION_CONTRACT_VERSION =
  "next_session_open_to_close_v2" as const;

export function hasCurrentExecutionContract(
  value: unknown,
): value is { execution_contract: ExecutionContractMetadata } {
  if (typeof value !== "object" || value === null) return false;
  const contract = (value as { execution_contract?: unknown }).execution_contract;
  if (typeof contract !== "object" || contract === null) return false;
  return (
    (contract as { contract_version?: unknown }).contract_version ===
    CURRENT_EXECUTION_CONTRACT_VERSION
  );
}
