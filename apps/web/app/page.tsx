import { cookies } from "next/headers";
import LandingPage from "./landing/landing-page";
import Workspace from "./workspace";

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const [{ auth, monitor, view }, session] = await Promise.all([
    searchParams,
    cookies(),
  ]);

  if (
    auth === "login" ||
    auth === "signup" ||
    auth === "reset" ||
    monitor ||
    view
  ) {
    return <Workspace />;
  }

  if (session.has("pg_access") || session.has("pg_refresh")) {
    return <Workspace signedOutPage={<LandingPage />} />;
  }

  return <LandingPage />;
}
