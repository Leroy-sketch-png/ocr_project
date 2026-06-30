import pytest
from src.table_builder import detect_column_boundaries
from src.models import Token

def test_detect_column_boundaries():
    # Mock token groups at known X positions
    # Group 1 around x=100
    g1 = [Token("A", 1, (90, 0, 110, 10), 100.0)]
    # Group 2 around x=120
    g2 = [Token("B", 1, (110, 0, 130, 10), 100.0)]
    # Group 3 around x=300
    g3 = [Token("C", 1, (290, 0, 310, 10), 100.0)]
    
    numeric_groups = [g1, g2, g3]
    
    # With gap_threshold=40, g1 and g2 should merge.
    # Center of g1 right edge is 110, g2 right edge is 130. Avg = 120.
    # g3 right edge is 310.
    col_centers = detect_column_boundaries(numeric_groups, 40)
    assert len(col_centers) == 2
    assert abs(col_centers[0] - 120.0) < 1.0
    assert abs(col_centers[1] - 310.0) < 1.0

    # Verify DPI scale=2.0 doubles the threshold (threshold=80)
    # If we have groups at 100, 150, 300
    # gap between 100 and 150 is 50.
    g4 = [Token("D", 1, (140, 0, 160, 10), 100.0)]
    numeric_groups_2 = [g1, g4, g3]
    
    # At threshold=40, they are separate (100, 150, 300) -> 3 columns
    assert len(detect_column_boundaries(numeric_groups_2, 40)) == 3
    
    # At threshold=80, g1 and g4 merge (100 and 150 merge, avg 125), g3 separate -> 2 columns
    assert len(detect_column_boundaries(numeric_groups_2, 80)) == 2
