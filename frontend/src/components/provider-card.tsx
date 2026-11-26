"use client";

import Link from "next/link";
import { AlertTriangle, ChevronRight, Plus } from "lucide-react";
import { Monogram } from "@/components/monogram";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { timeAgo } from "@/lib/format";
import type { CatalogProvider, ConnectionInfo } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Props {
  provider: CatalogProvider;
  connecting: boolean;
  onConnect: (provider: CatalogProvider) => void;
  onToggle: (connection: ConnectionInfo, enabled: boolean) => void;
}

export function ProviderCard({ provider, connecting, onConnect, onToggle }: Props) {
  const connected = provider.connections.length > 0;
  const needsSetup = provider.supported && !provider.integration_key;
  const canConnect = provider.supported && !!provider.integration_key;

  return (
    <article
      className={cn(
        "flex flex-col gap-4 rounded-lg border bg-card p-5 shadow-[0_1px_2px_oklch(0.3_0.05_90/0.06)]",
        !provider.supported && "bg-card/50",
      )}
    >
      <div className="flex items-start gap-3.5">
        <Monogram name={provider.name} muted={!provider.supported} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-serif text-xl font-semibold leading-tight">{provider.name}</h3>
            {connected && <Badge variant="success">Connected</Badge>}
            {!provider.supported && <Badge>Coming soon</Badge>}
            {needsSetup && <Badge variant="warning">Setup needed</Badge>}
          </div>
          <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{provider.description}</p>
        </div>
      </div>

      {connected && (
        <ul className="divide-y rounded-md border bg-background/60">
          {provider.connections.map((connection, index) => (
            <li key={connection.id} className="flex items-center gap-3 px-3.5 py-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">Account {provider.connections.length > 1 ? index + 1 : ""}</p>
                {connection.status === "ERROR" ? (
                  <p className="flex items-center gap-1 text-xs text-destructive">
                    <AlertTriangle className="size-3" /> Sign-in needs to be renewed
                  </p>
                ) : connection.last_error ? (
                  <p className="truncate text-xs text-destructive" title={connection.last_error}>
                    {connection.last_error}
                  </p>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    {connection.last_synced_at ? `Synced ${timeAgo(connection.last_synced_at)}` : "Waiting for the first sync"}
                  </p>
                )}
              </div>
              {connection.status === "ERROR" ? (
                <Button size="sm" variant="outline" onClick={() => onConnect(provider)} disabled={connecting}>
                  Reconnect
                </Button>
              ) : (
                <Switch
                  checked={connection.enabled}
                  onCheckedChange={(value) => onToggle(connection, value)}
                  aria-label={`Sync ${provider.name} account`}
                />
              )}
              <Link
                href={`/connectors/${connection.id}`}
                aria-label={`Manage ${provider.name} account`}
                className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              >
                <ChevronRight className="size-4" />
              </Link>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-auto">
        {canConnect && !connected && (
          <Button onClick={() => onConnect(provider)} disabled={connecting} className="w-full">
            Connect {provider.name}
          </Button>
        )}
        {canConnect && connected && (
          <button
            type="button"
            onClick={() => onConnect(provider)}
            disabled={connecting}
            className="flex w-full items-center justify-center gap-2 rounded-md border border-dashed border-input px-3 py-2 text-sm text-muted-foreground transition-colors hover:border-brass hover:text-foreground disabled:opacity-50"
          >
            <Plus className="size-4" /> Connect another account
          </button>
        )}
        {needsSetup && (
          <p className="text-xs leading-relaxed text-muted-foreground">
            Add a {provider.name} integration in the Nango dashboard first (see <span className="font-medium">docs/NANGO_SETUP.md</span>).
          </p>
        )}
      </div>
    </article>
  );
}
