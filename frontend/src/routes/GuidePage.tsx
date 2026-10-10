function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-4 rounded-xl border border-rd-line bg-rd-surface p-4 shadow-xl">
      <h3 className="text-lg font-semibold">{title}</h3>
      <div className="mt-2 flex flex-col gap-2 text-sm text-rd-text">{children}</div>
    </section>
  )
}

function Code({ children }: { children: React.ReactNode }) {
  return <code className="rounded bg-rd-input px-1.5 py-0.5 font-mono text-xs">{children}</code>
}

export default function GuidePage() {
  return (
    <div>
      <h2 className="text-xl font-semibold">Guide</h2>
      <p className="mt-1 text-sm text-rd-muted">
        Everything this app does, in five minutes.
      </p>
      <Block title="1 · Shortcuts">
        <p>
          A shortcut maps a short <Code>pattern</Code> to a <Code>target</Code> URL.
          Visit <Code>/docs</Code> and you land on the target — instantly, or after
          a short countdown page.
        </p>
        <p><strong>Static:</strong> <Code>docs</Code> → <Code>https://x.example/docs</Code>.</p>
        <p>
          <strong>Dynamic:</strong> one pattern with a placeholder, e.g. <Code>jira</Code> →{' '}
          <Code>{'https://j.example/{ticket}'}</Code>. Visiting <Code>/jira/ABC-123</Code>{' '}
          substitutes the argument into the target.
        </p>
        <p>
          <strong>User-dynamic:</strong> like dynamic, but the argument must match a
          configured parameter (see the shortcut&apos;s params).
        </p>
      </Block>
      <Block title="2 · Visibility and expiry">
        <p>
          <Code>public</Code> links resolve for everyone; <Code>unlisted</Code> links
          work but stay out of lists; <Code>private</Code> and <Code>team</Code> links
          need the owner or an admin. <Code>expires_at</Code> retires a link
          automatically (it then answers 410 Gone).
        </p>
      </Block>
      <Block title="3 · Upstreams">
        <p>
          Upstreams are other shortener namespaces checked before a pattern is
          treated as unknown. Add one under Upstreams, then use the live check
          to watch the fan-out stream event by event. Cache entries can be
          resynced or purged per pattern.
        </p>
      </Block>
      <Block title="4 · Sharing">
        <p>
          Every shortcut has a QR code (<Code>/qr/&lt;pattern&gt;</Code>) for
          print and rooms. Metrics shows hits, popular links and cache health.
        </p>
      </Block>
      <Block title="5 · Automation">
        <p>
          Everything clickable here is an API call under <Code>/api/v1</Code> —
          issue an API key on the Admin page and drive it from scripts
          (interactive docs at <Code>/docs</Code>). Backups are versioned
          archives you can download, delete and restore from Admin.
        </p>
      </Block>
    </div>
  )
}
