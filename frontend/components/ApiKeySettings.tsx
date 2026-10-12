"use client";

import { useCallback, useEffect, useState } from "react";
import { KeyIcon, SpinnerIcon, TrashIcon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Notice } from "@/components/ui/notice";
import { PROVIDER_LABELS, PROVIDERS, type ProviderId } from "@/lib/api-key-store";
import { KEY_PATTERNS, validateApiKey } from "@/lib/validateApiKey";
import {
  deleteApiKey,
  listSavedApiKeys,
  saveApiKey,
  setApiKeyPriority,
  type SavedApiKey,
} from "@/app/lib/actions/apiKeys";

/**
 * Manage "bring your own" AI provider keys.
 *
 * The plaintext key travels from this browser to the backend once, over HTTPS.
 * The server derives a per-user encryption key (PBKDF2 over `user_id +
 * MASTER_SECRET`, computed fresh and never stored), encrypts the key with
 * AES-256-GCM, and stores only the blob + a last-4 hint. The plaintext never
 * lives in this browser afterwards: this tab only remembers which saved
 * provider to ask with, and /chat sends just that id — the backend picks one
 * available key for the provider (rotating around exhausted ones) and decrypts
 * it server-side for each request.
 */
export function ApiKeySettings() {
  const [saved, setSaved] = useState<SavedApiKey[]>([]);
  const [loadingKeys, setLoadingKeys] = useState(true);
  const [loadError, setLoadError] = useState("");

  // Add form.
  const [provider, setProvider] = useState<ProviderId>("openrouter");
  const [keyValue, setKeyValue] = useState("");
  const [labelValue, setLabelValue] = useState("");
  const [saving, setSaving] = useState(false);

  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const { valid: keyFormatValid, error: formatError } = validateApiKey(provider, keyValue);

  const refresh = useCallback(() => {
    listSavedApiKeys().then((result) => {
      setLoadingKeys(false);
      if (!result.ok) {
        setLoadError(result.error);
        setSaved([]);
        return;
      }
      setLoadError("");
      setSaved(result.keys);
    });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleAddKey(e: React.FormEvent) {
    e.preventDefault();
    if (!keyValue.trim() || saving) return;

    setSaving(true);
    setNotice(null);
    try {
      const result = await saveApiKey(provider, keyValue.trim(), labelValue || undefined);

      if (!result.ok) {
        setNotice({ tone: "error", text: result.error });
        return;
      }

      const name = result.key.label?.trim() || PROVIDER_LABELS[provider];
      setNotice({
        tone: "success",
        text: `Saved ${name} key (${result.key.keyHint}).`,
      });
      refresh();
    } catch (err) {
      setNotice({
        tone: "error",
        text: err instanceof Error ? err.message : "Could not save your key. Try again.",
      });
    } finally {
      // Clear the key after the attempt, success or failure: the secret must
      // not linger in the field.
      setKeyValue("");
      setLabelValue("");
      setSaving(false);
    }
  }

  async function handleDelete(row: SavedApiKey) {
    setNotice(null);
    const result = await deleteApiKey(row.id);

    if (!result.ok) {
      setNotice({ tone: "error", text: result.error });
      return;
    }

    // Deleting one of several keys for a provider never stops you using the
    // others, so the active provider is left untouched.
    const name = row.label?.trim() || PROVIDER_LABELS[row.provider];
    setNotice({
      tone: "success",
      text: `Removed ${name} key (${row.keyHint}).`,
    });
    refresh();
  }

  async function handleBumpPriority(row: SavedApiKey) {
    setNotice(null);
    const result = await setApiKeyPriority(row.id, row.priority + 1);

    if (!result.ok) {
      setNotice({ tone: "error", text: result.error });
      return;
    }

    refresh();
  }

  return (
    <div className="mx-auto w-full max-w-[640px] px-5 pb-16 pt-8">
      <div className="mb-6">
        <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.09em] text-accent">
          Bring your own key
        </p>
        <h1 className="text-[22px] font-semibold leading-tight tracking-[-0.02em] text-foreground">
          API keys
        </h1>
        <p className="mt-2 text-[13px] leading-relaxed text-muted">
          Your provider key is sent to the server once, over HTTPS, and the keys
          are encrypted at rest with a key derived from your account and the
          server&apos;s secret — the raw key is never stored. This tab just
          remembers which saved provider to ask with.
        </p>
      </div>

      <div className="space-y-6">
        {notice ? <Notice tone={notice.tone}>{notice.text}</Notice> : null}

        <section className="rounded-2xl border border-border-base bg-surface p-5">
          <h2 className="mb-3 text-[15px] font-semibold text-foreground">Saved keys</h2>

          {loadingKeys ? (
            <p className="flex items-center gap-1.5 text-[12.5px] text-muted">
              <SpinnerIcon className="h-3.5 w-3.5" />
              Loading…
            </p>
          ) : loadError ? (
            <Notice tone="error">{loadError}</Notice>
          ) : saved.length === 0 ? (
            <p className="text-[13px] text-muted">
              No keys saved yet. Add one below — it will be encrypted on the
              server before it reaches the key store.
            </p>
          ) : (
            <ul className="space-y-2.5">
              {saved.map((row) => (
                <li
                  key={row.id}
                  className="flex items-center justify-between gap-3 rounded-xl border border-border-base bg-surface-subtle px-3.5 py-2.5"
                >
                  <div className="min-w-0">
                    <p className="flex items-center gap-1.5 text-[13px] font-medium text-foreground">
                      <KeyIcon className="h-3.5 w-3.5 shrink-0 text-muted" />
                      <span className="truncate">
                        {row.label?.trim() || PROVIDER_LABELS[row.provider]}
                      </span>
                      {row.isExhausted ? (
                        <span className="shrink-0 rounded-md bg-amber-500/15 px-1.5 py-0.5 text-[10.5px] font-medium text-amber-700 dark:text-amber-300">
                          Rate limited
                        </span>
                      ) : null}
                    </p>
                    <p className="mt-0.5 font-mono text-[12px] text-muted">
                      {PROVIDER_LABELS[row.provider]} · …{row.keyHint}
                    </p>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={`Raise priority of ${row.label?.trim() || PROVIDER_LABELS[row.provider]} key`}
                      title="Raise priority (tried first)"
                      onClick={() => handleBumpPriority(row)}
                    >
                      ↑
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={`Delete ${row.label?.trim() || PROVIDER_LABELS[row.provider]} key`}
                      title="Delete stored key"
                      onClick={() => handleDelete(row)}
                    >
                      <TrashIcon className="h-4 w-4" />
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="rounded-2xl border border-border-base bg-surface p-5">
          <h2 className="mb-3 text-[15px] font-semibold text-foreground">
            Add a key
          </h2>
          <p className="mb-4 text-[12.5px] leading-relaxed text-muted">
            You can keep several keys per provider — the server rotates between
            them and skips any that are rate-limited.
          </p>

          <form onSubmit={handleAddKey} className="space-y-4">
            <div>
              <label
                htmlFor="key-provider"
                className="mb-1.5 block text-[12.5px] font-medium text-foreground"
              >
                Provider
              </label>
              <select
                id="key-provider"
                value={provider}
                onChange={(e) => setProvider(e.target.value as ProviderId)}
                className="h-11 w-full rounded-xl border border-border-base bg-surface px-3.5 text-sm text-foreground outline-none focus:border-accent focus:ring-4 focus:ring-accent/12"
              >
                {PROVIDERS.map((p) => (
                  <option key={p} value={p}>
                    {PROVIDER_LABELS[p]}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label
                htmlFor="key-label"
                className="mb-1.5 block text-[12.5px] font-medium text-foreground"
              >
                Label <span className="font-normal text-muted">(optional)</span>
              </label>
              <Input
                id="key-label"
                type="text"
                maxLength={60}
                placeholder="e.g. Work, Personal"
                value={labelValue}
                onChange={(e) => setLabelValue(e.target.value)}
              />
            </div>

            <div>
              <label
                htmlFor="key-value"
                className="mb-1.5 block text-[12.5px] font-medium text-foreground"
              >
                API key
              </label>
              <Input
                id="key-value"
                type="password"
                autoComplete="off"
                value={keyValue}
                onChange={(e) => setKeyValue(e.target.value)}
                placeholder={KEY_PATTERNS[provider].example}
                aria-invalid={keyValue.trim() !== "" && formatError !== null}
              />
              {keyValue.trim() && formatError ? (
                <p className="mt-1.5 text-[12px] leading-relaxed text-red-600 dark:text-red-400">
                  {formatError}
                </p>
              ) : null}
              <p className="mt-1.5 text-[11.5px] leading-relaxed text-muted">
                Sent once, over HTTPS, and checked with the provider itself
                before it is saved.
              </p>
            </div>

            <div className="flex items-center justify-end gap-2.5">
              <Button
                type="submit"
                size="md"
                disabled={!keyFormatValid || saving}
              >
                {saving ? <SpinnerIcon className="h-4 w-4" /> : "Save key"}
              </Button>
            </div>
          </form>
        </section>

        <p className="text-center text-[12px] leading-relaxed text-muted">
          Provider keys are yours. We store them encrypted and pick one of your
          saved keys each time you ask a question — rotating around any that are
          rate-limited.
        </p>
      </div>
    </div>
  );
}