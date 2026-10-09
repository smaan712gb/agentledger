import type { Client, ClientDetail, Engagement, Me } from "@agentledger/contracts";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useId } from "react";

import { isClient, isFirmStaff } from "../auth/can";
import { useMe } from "../auth/AuthProvider";
import { queries } from "../queries";
import { Chip } from "../ui/Chip";
import { basisOf, currentYear, frozenState, periodChoices, periodLabel, type ClientSearch } from "./contextState";
import styles from "./ContextBar.module.css";

export interface ContextBarState {
  clientId: string;
  year: number;
  engagementId: number | undefined;
  closedThrough: string | null;
  frozen: ReturnType<typeof frozenState>;
  basis: ReturnType<typeof basisOf>;
  label: string;
}

/** The current context from the URL and the loaded client; screens read this and put `year` in their query keys. */
export function useContextBar(detail?: ClientDetail): ContextBarState {
  const { clientId } = useParams({ from: "/_app/clients/$clientId" });
  const search: ClientSearch = useSearch({ from: "/_app/clients/$clientId" });
  const year = search.year ?? currentYear();
  const closedThrough = detail?.client.closed_through ?? null;
  return {
    clientId,
    year,
    engagementId: search.engagement,
    closedThrough,
    frozen: frozenState(year, closedThrough),
    basis: basisOf(detail?.client.facts),
    label: periodLabel(year, closedThrough),
  };
}

export function ContextBar({ detail, clients }: { detail: ClientDetail | undefined; clients: Client[] | undefined }) {
  const me = useMe();
  const state = useContextBar(detail);
  const navigate = useNavigate();
  const ids = { firm: useId(), entity: useId(), engagement: useId(), period: useId() };
  const pipeline = useQuery({ ...queries.pipeline(), enabled: isFirmStaff(me) });
  const engagements: Engagement[] = pipeline.data
    ? Object.values(pipeline.data)
        .flat()
        .filter((e) => e.client_id === state.clientId)
    : [];
  const selectedEngagement = engagements.find((e) => e.id === state.engagementId);

  return (
    <nav className={styles.bar} aria-label="Context">
      <div className={styles.item}>
        <span className={styles.label} id={ids.firm}>
          Firm
        </span>
        <span className={styles.value} aria-labelledby={ids.firm} data-testid="context-firm">
          {firmLabel(me)}
        </span>
      </div>

      <div className={styles.item}>
        <label className={styles.label} htmlFor={ids.entity}>
          Entity
        </label>
        {clients && clients.length > 1 ? (
          <select
            id={ids.entity}
            className={styles.select}
            value={state.clientId}
            onChange={(e) =>
              void navigate({ to: "/clients/$clientId", params: { clientId: e.target.value }, search: (prev) => prev })
            }
          >
            {clients.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        ) : (
          <span className={styles.value} id={ids.entity}>
            {detail?.client.name ?? state.clientId}
          </span>
        )}
      </div>

      {isFirmStaff(me) ? (
        <div className={styles.item}>
          <label className={styles.label} htmlFor={ids.engagement}>
            Engagement
          </label>
          <select
            id={ids.engagement}
            className={styles.select}
            value={state.engagementId ?? ""}
            onChange={(e) =>
              void navigate({
                to: ".",
                search: (prev: ClientSearch) => ({
                  ...prev,
                  engagement: e.target.value ? Number(e.target.value) : undefined,
                }),
              })
            }
          >
            <option value="">No engagement</option>
            {engagements.map((e) => (
              <option key={e.id} value={e.id}>
                {e.type}
                {e.tax_year ? ` ${e.tax_year}` : ""} · {e.stage.replaceAll("_", " ")}
              </option>
            ))}
            {state.engagementId && !selectedEngagement ? (
              <option value={state.engagementId}>#{state.engagementId}</option>
            ) : null}
          </select>
        </div>
      ) : null}

      <div className={styles.item}>
        <label className={styles.label} htmlFor={ids.period}>
          Period
        </label>
        <span className={styles.periodGroup}>
          <select
            id={ids.period}
            className={styles.select}
            value={state.year}
            onChange={(e) =>
              void navigate({ to: ".", search: (prev: ClientSearch) => ({ ...prev, year: Number(e.target.value) }) })
            }
          >
            {periodChoices(state.year).map((y) => (
              <option key={y} value={y}>
                FY{y}
              </option>
            ))}
          </select>
          <span className={styles.hint} data-testid="context-period">
            {detail ? state.label : `FY${state.year}`}
          </span>
          {!detail ? null : state.frozen.frozen ? (
            <Chip tone="info" title={state.frozen.reason ?? undefined}>
              frozen
            </Chip>
          ) : state.frozen.partial ? (
            <Chip tone="info" title={state.frozen.reason ?? undefined}>
              partly closed
            </Chip>
          ) : null}
        </span>
      </div>

      <div className={styles.item}>
        <span className={styles.label}>Basis</span>
        <span className={styles.value} data-testid="context-basis" aria-busy={!detail || undefined}>
          {!detail ? (
            <span className={styles.hint}>…</span>
          ) : state.basis.recorded ? (
            <Chip tone="good">{state.basis.basis}</Chip>
          ) : isClient(me) ? (
            <Chip tone="warn">basis not recorded</Chip>
          ) : (
            <Link
              to="/clients/$clientId/profile"
              params={{ clientId: state.clientId }}
              search={(prev) => prev}
              className={styles.chipLink}
              title="Record the accounting basis in the profile"
            >
              <Chip tone="warn">basis not recorded</Chip>
            </Link>
          )}
        </span>
      </div>
    </nav>
  );
}

/** The firm's name is not in GET /api/me yet (docs/WEB.md); the id is shown honestly instead. */
export function firmLabel(me: Me): string {
  return me.firm_id;
}
