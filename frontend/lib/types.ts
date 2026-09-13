export type Message = { role: "user" | "assistant" | "error"; content: string };
export type Profile = {
  name: string;
  place: string;
  birth_datetime: string;
  time_accuracy: "exact" | "hour_known" | "unknown";
};
export type Pillar = { stem: string; branch: string };
export type BaziChart = {
  profile: Profile;
  clock_time: string;
  year_pillar: Pillar;
  month_pillar: Pillar;
  day_pillar: Pillar;
  time_pillar: Pillar | null;
  day_master: string;
  school: string;
  strength?: { verdict: string };
  warnings: string[];
};
export type AstroChart = {
  profile: Profile;
  house_system: string;
  zodiac_type: string;
  warnings: string[];
  points: Array<{
    key: string;
    name: string;
    sign: string;
    position: number;
    house: number | null;
    retrograde: boolean;
  }>;
};
export type RunResult = {
  reply: string;
  response_status: string;
  response_flags: unknown[];
  safety_flags: string[];
  charts: { bazi?: BaziChart; astro?: AstroChart };
};
export type ConversationSummary = {
  id: string;
  title: string;
  busy: boolean;
  failed: boolean;
};
export type Conversation = ConversationSummary & {
  messages: Message[];
  result: RunResult | null;
};
export type Bootstrap = {
  storage?: "memory" | "database";
  csrf_token: string;
  conversations: ConversationSummary[];
};
export type StreamEvent = {
  event: "update" | "final" | "error";
  schema_version?: number;
  data: { status?: string; message?: string };
};
