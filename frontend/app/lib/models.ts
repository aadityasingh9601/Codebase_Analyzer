// lib/models.ts

export const PROVIDER_MODELS = {
    openai: [
        { id: "gpt-4o", name: "GPT-4o", description: "Most capable" },
        { id: "gpt-4o-mini", name: "GPT-4o Mini", description: "Fast & cheap" },
        { id: "gpt-4.1", name: "GPT-4.1", description: "Latest" },
    ],
    anthropic: [
        { id: "claude-sonnet-4-6", name: "Claude Sonnet 4.6", description: "Smart & fast" },
        { id: "claude-opus-4-5", name: "Claude Opus 4.5", description: "Most capable" },
        { id: "claude-haiku-5-5", name: "Claude Haiku 5.5", description: "Fastest" },
    ],
    openrouter: [
        { id: "google/gemini-2.5-flash", name: "Gemini 2.5 Flash", description: "Fast, free tier" },
        { id: "meta-llama/llama-3.1-70b-instruct", name: "Llama 3.1 70B", description: "Open source" },
        { id: "deepseek/deepseek-r1", name: "DeepSeek R1", description: "Strong reasoning" },
        { id: "anthropic/claude-sonnet-4-6", name: "Claude via OpenRouter", description: "" },
        { id: "openai/gpt-4o", name: "GPT-4o via OpenRouter", description: "" },
    ]
}