import OpenAI from "openai";
import { routerOptions } from "./lib/routerRuntime";

// patched by an earlier run: left alone
export const a = new OpenAI({ apiKey: "k", ...routerOptions() });

// added since the last run
export const b = new OpenAI();

const myFetch = (u: any, i?: any) => fetch(u, i);
export const c = new OpenAI({ fetch: myFetch });
