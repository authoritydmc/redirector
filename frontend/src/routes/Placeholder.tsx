export default function Placeholder({ name }: { name: string }) {
  return (
    <section className="mx-auto mt-10 max-w-md rounded-2xl border border-rd-line bg-rd-surface p-8 text-center shadow-xl">
      <h2 className="text-xl font-semibold text-rd-text">{name}</h2>
      <p className="mt-2 text-sm text-rd-muted">
        This section is still under construction — it will plug into the same API as the rest of the app.
      </p>
    </section>
  )
}
