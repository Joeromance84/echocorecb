import java.util.HashMap;
import java.util.Map;

/**
 * Intelligent AI Router - Cost-Optimized AI Service Selection.
 * Routes AI requests to the most cost-effective provider.
 */
public class IntelligentAIRouter {

    private Map<String, Map<String, Object>> providers;
    private Map<String, Integer> usageStats;

    public IntelligentAIRouter() {
        this.providers = new HashMap<>();
        
        Map<String, Object> googleDetails = new HashMap<>();
        googleDetails.put("cost", 0.0);
        googleDetails.put("available", true);
        googleDetails.put("priority", 1);
        this.providers.put("google", googleDetails);

        Map<String, Object> openaiDetails = new HashMap<>();
        openaiDetails.put("cost", 0.002);
        openaiDetails.put("available", true);
        openaiDetails.put("priority", 2);
        this.providers.put("openai", openaiDetails);

        this.usageStats = new HashMap<>();
        this.usageStats.put("google", 0);
        this.usageStats.put("openai", 0);
    }

    /**
     * Routes a request to the optimal provider based on cost.
     *
     * @param requestType The type of request (e.g., "text"). Not currently used in logic but kept for future expansion.
     * @return The name of the selected provider.
     */
    public String routeRequest(String requestType) {
        if ((boolean) this.providers.get("google").get("available")) {
            this.usageStats.put("google", this.usageStats.get("google") + 1);
            return "google";
        } else {
            this.usageStats.put("openai", this.usageStats.get("openai") + 1);
            return "openai";
        }
    }

    /**
     * Calculates the cost savings from using the intelligent routing.
     *
     * @return A map containing cost savings metrics.
     */
    public Map<String, Object> getCostSavings() {
        int googleRequests = this.usageStats.get("google");
        double savings = googleRequests * 0.002;
        int totalRequests = this.usageStats.get("google") + this.usageStats.get("openai");
        
        String optimizationRate;
        if (totalRequests > 0) {
            optimizationRate = String.format("%.1f%%", ((double) googleRequests / totalRequests) * 100);
        } else {
            optimizationRate = "0.0%";
        }

        Map<String, Object> savingsData = new HashMap<>();
        savingsData.put("total_savings", savings);
        savingsData.put("free_requests", googleRequests);
        savingsData.put("optimization_rate", optimizationRate);
        
        return savingsData;
    }

    // A main method for demonstration
    public static void main(String[] args) {
        IntelligentAIRouter router = new IntelligentAIRouter();

        System.out.println("Routing 10 requests...");
        for (int i = 0; i < 10; i++) {
            String provider = router.routeRequest("text");
            System.out.println("Request routed to: " + provider);
        }

        Map<String, Object> stats = router.getCostSavings();
        System.out.println("\n--- Cost Savings Report ---");
        System.out.println("Total Requests: " + (int)stats.get("free_requests") + " free, " + (10 - (int)stats.get("free_requests")) + " paid.");
        System.out.println("Total Savings: $" + String.format("%.3f", (double) stats.get("total_savings")));
        System.out.println("Optimization Rate: " + stats.get("optimization_rate"));
    }
}
