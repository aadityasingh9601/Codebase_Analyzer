"use client";

import { PROVIDER_MODELS } from "@/app/lib/models";
import { PROVIDER_LABELS, PROVIDERS, type ProviderId } from "@/lib/api-key-store";

export type ProviderModelSelection = {
  provider: ProviderId;
  model: string;
};

/** Default first choice for a provider (the same rule the selector uses when a
 *  provider changes). ChatView mirrors this so the initial value matches. */
export function firstModel(provider: ProviderId): string {
  return PROVIDER_MODELS[provider][0].id;
}

const selectClass =
  "h-[38px] rounded-xl border border-border-base bg-surface px-3 text-[13px] text-foreground outline-none focus:border-accent focus:ring-4 focus:ring-accent/12 disabled:opacity-60";

/** Provider + model dropdowns for the chat composer. Picking a provider
 *  resets the model to that provider's first model. Controlled: state lives in
 *  the caller (ChatView), which also owns the current values. */
export function ProviderModelSelector({
  value,
  onChange,
  disabled = false,
  lockedProvider,
}: {
  value: ProviderModelSelection;
  onChange: (selection: ProviderModelSelection) => void;
  disabled?: boolean;
  lockedProvider?: ProviderId;
}) {
  const provider = lockedProvider ?? value.provider;
  function handleProviderChange(next: string) {
    const p = next as ProviderId;
    onChange({ provider: p, model: firstModel(p) });
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        value={provider}
        onChange={(e) => handleProviderChange(e.target.value)}
        disabled={disabled || !!lockedProvider}
        aria-label="Model provider"
        className={selectClass}
      >
        {PROVIDERS.map((p) => (
          <option key={p} value={p}>
            {PROVIDER_LABELS[p]}
          </option>
        ))}
      </select>

      <select
        value={value.model}
        onChange={(e) => onChange({ provider, model: e.target.value })}
        disabled={disabled}
        aria-label="Model"
        className={selectClass}
      >
        {PROVIDER_MODELS[provider].map((m) => (
          <option key={m.id} value={m.id}>
            {m.name}
          </option>
        ))}
      </select>
    </div>
  );
}