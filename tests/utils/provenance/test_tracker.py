import unittest
from datetime import datetime, timedelta, timezone
from backend.utils.provenance.tracker import DataProvenance, TraceableArtifact

class TestDataProvenance(unittest.TestCase):

    def test_provenance_tracking(self):
        # Create an artifact (e.g., a trading signal)
        signal = TraceableArtifact(artifact_id="sig_123", is_llm_generated=False)
        
        # Attach market data provenance
        market_data_prov = DataProvenance(
            source_name="Alpaca Market Data",
            is_stale=False,
            quality_score=0.99,
            raw_reference="request_id_abc"
        )
        signal.add_provenance(market_data_prov)
        
        # Attach fundamental data provenance (simulating stale data)
        fundamental_prov = DataProvenance(
            source_name="SEC Edgar",
            fetch_time=datetime.now(timezone.utc) - timedelta(days=90),
            is_stale=True, # Flagged as stale because it's old
            quality_score=0.5
        )
        signal.add_provenance(fundamental_prov)

        # Asserts
        self.assertEqual(len(signal.provenance_chain), 2)
        
        # The overall artifact should be considered stale if ANY dependency is stale
        self.assertTrue(signal.check_staleness())
        
        # The lowest quality score dictates the confidence
        self.assertEqual(signal.get_lowest_quality_score(), 0.5)

    def test_llm_artifact(self):
        analysis = TraceableArtifact(
            artifact_id="analysis_456",
            is_llm_generated=True,
            llm_prompt_ref="prompt_hash_xyz"
        )
        
        llm_prov = DataProvenance(
            source_name="OpenAI gpt-5.6",
            assumptions={"temperature": 0.0, "max_tokens": 1000}
        )
        analysis.add_provenance(llm_prov)
        
        self.assertTrue(analysis.is_llm_generated)
        self.assertIsNotNone(analysis.llm_prompt_ref)
        self.assertEqual(analysis.provenance_chain[0].assumptions["temperature"], 0.0)

if __name__ == '__main__':
    unittest.main()
