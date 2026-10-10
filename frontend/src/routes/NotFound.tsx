import { Link } from 'react-router-dom'

export default function NotFound() {
  return (
    <section className="mx-auto mt-10 max-w-md rounded-2xl border border-rd-line bg-rd-surface p-8 text-center shadow-xl">
      <p className="text-5xl font-black text-rd-muted" aria-hidden="true">404</p>
      <h2 className="mt-2 text-xl font-semibold text-rd-text">Page not found</h2>
      <p className="mt-2 text-sm text-rd-muted">
        This address doesn&apos;t exist in the app. Shortcuts themselves still resolve from the root path.
      </p>
      <Link
        to="/"
        className="mt-4 inline-block rounded bg-rd-accent px-4 py-2 text-sm text-rd-accent-ink"
      >
        Back to Shortcuts
      </Link>
    </section>
  )
}
