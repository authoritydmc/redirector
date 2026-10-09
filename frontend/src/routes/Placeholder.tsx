export default function Placeholder({ name }: { name: string }) {
  return (
    <section>
      <h2 className="text-xl font-semibold">{name}</h2>
      <p className="mt-2 text-sm opacity-70">Coming soon.</p>
    </section>
  )
}
