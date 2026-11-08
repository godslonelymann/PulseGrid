import {
  Activity,
  ChevronDown,
  Globe,
  ChartNoAxesCombined,
  Bell,
} from "lucide-react";
import localFont from "next/font/local";
import Link from "next/link";
import { IncidentHero, CheckHistory } from "./dashboard-preview";
import { GoogleCallback } from "./landing-interactions";
import styles from "./landing.module.css";

const geist = localFont({
  src: "./fonts/Geist-Variable.woff2",
  weight: "100 900",
  variable: "--landing-sans",
  display: "swap",
});
const geistMono = localFont({
  src: "./fonts/GeistMono-Variable.woff2",
  weight: "100 900",
  variable: "--landing-mono",
  display: "swap",
  preload: false,
});

const repository = "https://github.com/godslonelymann/PulseGrid";

function Brand() {
  return (
    <Link className={styles.brand} href="#top" aria-label="PulseGrid home">
      <Activity size={28} aria-hidden="true" />
      PulseGrid
    </Link>
  );
}

export default function LandingPage() {
  return (
    <div
      id="top"
      data-landing
      className={`${styles.landing} ${geist.variable} ${geistMono.variable}`}
    >
      <GoogleCallback />
      <header className={styles.header}>
        <div className={`${styles.container} ${styles.bar}`}>
          <Brand />
          <nav className={styles.mainNav} aria-label="Main">
            <a href="#product">Product</a>
            <a href="#setup">Setup</a>
          </nav>
          <div className={styles.links}>
            <Link href="/?auth=login" prefetch={false}>
              Get Started
            </Link>
            <Link
              className={`${styles.primary} ${styles.headerCta}`}
              href="/?auth=signup"
              prefetch={false}
            >
              Start monitoring
            </Link>
          </div>
        </div>
      </header>
      <main>
        <div className={styles.heroSurface}>
          <IncidentHero />
        </div>
        <section
          id="product"
          className={`${styles.container} ${styles.evidence}`}
          aria-labelledby="product-title"
        >
          <div className={styles.sectionCopy}>
            <span className={styles.eyebrow}>EVERY CHECK TELLS A STORY</span>
            <h2 id="product-title">See what happened.</h2>
            <p>
              From the first failed check to a confirmed recovery. Response
              times and incident history, together in one place.
            </p>
            <div className={styles.rule}>
              <span>02</span> consecutive checks to confirm an outage or
              recovery
            </div>
          </div>
          <CheckHistory />
        </section>
        <section className={styles.capabilities} aria-label="Capabilities">
          <div className={`${styles.container} ${styles.featureGrid}`}>
            {[
              {
                Icon: Globe,
                title: "Check any HTTP endpoint",
                copy: "Monitor your websites and APIs from your own server.",
              },
              {
                Icon: ChartNoAxesCombined,
                title: "Review outages and slowdowns",
                copy: "See response times, failed checks, and the full incident history.",
              },
              {
                Icon: Bell,
                title: "Get outage and recovery alerts",
                copy: "Send email or webhook alerts when an incident opens or closes.",
              },
            ].map(({ Icon, title, copy }) => (
              <div className={styles.feature} key={title}>
                <Icon size={22} aria-hidden="true" />
                <h3>{title}</h3>
                <p>{copy}</p>
              </div>
            ))}
          </div>
        </section>
        <section
          id="setup"
          className={`${styles.container} ${styles.setup}`}
          aria-label="Setup questions"
        >
          <details className={styles.faq}>
            <summary>
              When does PulseGrid open an incident?
              <ChevronDown size={16} aria-hidden="true" />
            </summary>
            <p>
              Two consecutive failed checks open an incident. Two consecutive
              successful checks close it. Each state change queues an alert for
              enabled destinations.
            </p>
          </details>
          <details className={styles.faq}>
            <summary>
              What do I need to self-host it?
              <ChevronDown size={16} aria-hidden="true" />
            </summary>
            <p>
              Run the web app, API, and monitoring worker on your own server
              with PostgreSQL and Redis. Follow the setup guide for installation
              and alert configuration.
            </p>
          </details>
        </section>
      </main>

      <footer className={styles.footer}>
        <div className={`${styles.container} ${styles.bar}`}>
          <Brand />
          <nav className={styles.links} aria-label="Footer">
            <a href={repository}>GitHub</a>
            <Link href="/?auth=login" prefetch={false}>
              Log in
            </Link>
          </nav>
        </div>
      </footer>
    </div>
  );
}
