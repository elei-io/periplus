import { LandingLauncher } from "@/components/landing-launcher"
import { LandingStory } from "@/components/landing-story"

export default function HomePage() {
  return <main className="discovery-shell">
    <section className="discovery-hero"><span className="eyebrow"><span className="identity-dot" />A public web observatory</span><h1>The web.<br /><span>Through an analytical lens.</span></h1><p>Periplus makes web structure queryable.{" "}<br className="hidden sm:block" />Bring an idea for a dataset. Use SQL to give that structure meaning.</p></section>
    <LandingLauncher />
    <LandingStory />
  </main>
}
