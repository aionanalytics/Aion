"""Unit tests for nightly job Phase 9 training split into 9A (Sector) and 9B (Global)."""

from __future__ import annotations

import pytest


class TestNightlyJobTrainingSplit:
    """Test suite for Phase 9 training split into Sector (9A) and Global (9B) phases."""
    
    def test_pipeline_has_separate_training_phases(self):
        """Test that PIPELINE has both training_sector and training_global phases."""
        from backend.jobs.nightly_job import PIPELINE
        
        # Extract phase keys
        phase_keys = [key for key, _title in PIPELINE]
        
        # Verify training_sector exists
        assert "training_sector" in phase_keys, "training_sector phase missing from PIPELINE"
        
        # Verify training_global exists
        assert "training_global" in phase_keys, "training_global phase missing from PIPELINE"
        
        # Verify old "training" phase is removed
        assert "training" not in phase_keys, "Old 'training' phase should be removed from PIPELINE"
    
    def test_pipeline_training_phases_in_correct_order(self):
        """Test that training_sector comes before training_global in PIPELINE."""
        from backend.jobs.nightly_job import PIPELINE
        
        phase_keys = [key for key, _title in PIPELINE]
        
        sector_index = phase_keys.index("training_sector")
        global_index = phase_keys.index("training_global")
        
        # Sector training should come before global training
        assert sector_index < global_index, "training_sector should come before training_global"
        
        # Verify they are consecutive
        assert global_index == sector_index + 1, "training phases should be consecutive"
    
    def test_pipeline_total_phases_count(self):
        """Test that TOTAL_PHASES reflects the correct count after split."""
        from backend.jobs.nightly_job import PIPELINE, TOTAL_PHASES
        
        # Count should be 22 (21 original + 1 for the split)
        assert len(PIPELINE) == 22, f"Expected 22 phases, got {len(PIPELINE)}"
        assert TOTAL_PHASES == 22, f"TOTAL_PHASES should be 22, got {TOTAL_PHASES}"
    
    def test_pipeline_training_phases_at_correct_indices(self):
        """Test that training phases are at indices 8 and 9."""
        from backend.jobs.nightly_job import PIPELINE
        
        # Index 8 should be training_sector
        assert PIPELINE[8][0] == "training_sector", f"PIPELINE[8] should be training_sector, got {PIPELINE[8][0]}"
        
        # Index 9 should be training_global
        assert PIPELINE[9][0] == "training_global", f"PIPELINE[9] should be training_global, got {PIPELINE[9][0]}"
    
    def test_pipeline_training_phase_titles(self):
        """Test that training phases have descriptive titles."""
        from backend.jobs.nightly_job import PIPELINE
        
        # Check training_sector title
        sector_title = PIPELINE[8][1]
        assert "sector" in sector_title.lower(), f"Sector training title should mention 'sector': {sector_title}"
        
        # Check training_global title
        global_title = PIPELINE[9][1]
        assert "global" in global_title.lower() or "universe" in global_title.lower(), \
            f"Global training title should mention 'global' or 'universe': {global_title}"
    
    def test_predictions_phase_follows_training(self):
        """Test that predictions phase follows both training phases."""
        from backend.jobs.nightly_job import PIPELINE
        
        phase_keys = [key for key, _title in PIPELINE]
        
        # Find indices
        global_index = phase_keys.index("training_global")
        predictions_index = phase_keys.index("predictions")
        
        # Predictions should come right after global training
        assert predictions_index == global_index + 1, \
            "predictions phase should immediately follow training_global"
    
    def test_pipeline_phase_order(self):
        """Test that all phases are in the expected order."""
        from backend.jobs.nightly_job import PIPELINE
        
        expected_order = [
            "load_rolling",
            "backfill",
            "fundamentals",
            "metrics",
            "macro",
            "social",
            "news_intel",
            "dataset",
            "training_sector",    # NEW: Phase 9A
            "training_global",    # NEW: Phase 9B
            "predictions",
            "prediction_logger",
            "accuracy_engine",
            "context",
            "regime",
            "continuous_learning",
            "performance",
            "aion_brain",
            "policy",
            "swing_bot_eod",
            "insights",
            "supervisor",
        ]
        
        actual_order = [key for key, _title in PIPELINE]
        
        assert actual_order == expected_order, \
            f"Phase order mismatch.\nExpected: {expected_order}\nActual: {actual_order}"
    
    def test_sector_training_failure_doesnt_block_global(self):
        """Test that sector training failure allows global training to continue."""
        # This is a design test - verify the code structure allows continuation
        import inspect
        from backend.jobs import nightly_job
        
        # Get the source code of the run_nightly_job function
        source = inspect.getsource(nightly_job.run_nightly_job)
        
        # Verify sector training has error handling that doesn't raise
        assert "except Exception as e:" in source, "Should have exception handling"
        assert "⚠️ Sector training failed (continuing with global)" in source, \
            "Sector training should allow continuation on failure"
    
    def test_global_training_failure_raises(self):
        """Test that global training failure raises an exception."""
        import inspect
        from backend.jobs import nightly_job
        
        # Get the source code of the run_nightly_job function
        source = inspect.getsource(nightly_job.run_nightly_job)
        
        # Find global training exception handler
        # It should have 'raise' to propagate the error
        lines = source.split('\n')
        in_global_training = False
        has_raise = False
        
        for i, line in enumerate(lines):
            if "9B) Global Training" in line:
                in_global_training = True
            elif in_global_training and "# 10) Predictions" in line:
                break
            elif in_global_training and "except Exception as e:" in line:
                # Check next few lines for 'raise'
                for j in range(i, min(i + 10, len(lines))):
                    if lines[j].strip() == "raise":
                        has_raise = True
                        break
        
        assert has_raise, "Global training should raise exception on failure"


class TestNightlyJobPhaseNumbering:
    """Test suite for correct phase numbering after training split."""
    
    def test_phase_numbers_match_display(self):
        """Test that phase numbers in code match what will be displayed."""
        import inspect
        from backend.jobs import nightly_job
        
        source = inspect.getsource(nightly_job.run_nightly_job)
        lines = source.split('\n')
        
        # Map of expected phase display numbers to their comments
        expected_phases = {
            "# 9A) Sector Training": "9",
            "# 9B) Global Training": "10",
            "# 10) Predictions": "11",
            "# 11) Prediction logger": "12",
            "# 12) Accuracy engine": "13",
            "# 13) Context state": "14",
            "# 14) Regime detection": "15",
            "# 15) Continuous learning": "16",
            "# 16) Performance aggregation": "17",
            "# 17) AION brain update": "18",
            "# 18) Policy engine": "19",
            "# 19) Swing Bot EOD": "20",
            "# 20) Insights builder": "21",
            "# 21) Supervisor agent": "22",
        }
        
        for i, line in enumerate(lines):
            for comment, expected_num in expected_phases.items():
                if comment in line:
                    # Find the _phase call within next 5 lines
                    for j in range(i, min(i + 5, len(lines))):
                        if "_phase(" in lines[j]:
                            # Extract the phase number from _phase(title, NUMBER, TOTAL_PHASES)
                            parts = lines[j].split(",")
                            if len(parts) >= 2:
                                phase_num = parts[1].strip()
                                assert phase_num == expected_num, \
                                    f"{comment} should display phase {expected_num}, got {phase_num}"
                            break
    
    def test_pipeline_indices_match_phase_calls(self):
        """Test that PIPELINE indices in code match the phase they represent."""
        import inspect
        from backend.jobs import nightly_job
        
        source = inspect.getsource(nightly_job.run_nightly_job)
        lines = source.split('\n')
        
        # Map of phase comments to expected PIPELINE index
        expected_indices = {
            "# 9A) Sector Training": "PIPELINE[8]",
            "# 9B) Global Training": "PIPELINE[9]",
            "# 10) Predictions": "PIPELINE[10]",
            "# 11) Prediction logger": "PIPELINE[11]",
            "# 12) Accuracy engine": "PIPELINE[12]",
            "# 13) Context state": "PIPELINE[13]",
            "# 14) Regime detection": "PIPELINE[14]",
            "# 15) Continuous learning": "PIPELINE[15]",
            "# 16) Performance aggregation": "PIPELINE[16]",
            "# 17) AION brain update": "PIPELINE[17]",
            "# 18) Policy engine": "PIPELINE[18]",
            "# 19) Swing Bot EOD": "PIPELINE[19]",
            "# 20) Insights builder": "PIPELINE[20]",
            "# 21) Supervisor agent": "PIPELINE[21]",
        }
        
        for i, line in enumerate(lines):
            for comment, expected_index in expected_indices.items():
                if comment in line:
                    # Find the PIPELINE access within next 5 lines
                    for j in range(i, min(i + 5, len(lines))):
                        if "PIPELINE[" in lines[j] and "=" in lines[j]:
                            assert expected_index in lines[j], \
                                f"{comment} should use {expected_index}, line: {lines[j].strip()}"
                            break
