package com.example.reporting;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/**
 * Tests for the LOB-based reporting filter.
 */
class ReportingUtilsTest {

    /**
     * The filter should keep records whose LOB matches the requested value.
     *
     * @return nothing
     */
    @Test
    void filterByLob_keepsMatchingRecords() {
        ReportingUtils utils = new ReportingUtils();
        var result = utils.filterByLob(sampleRecords(), "ENTERPRISE");
        assertEquals(2, result.size());
    }

    @Test
    void filterByLob_returnsEmptyWhenNoMatch() {
        ReportingUtils utils = new ReportingUtils();
        var result = utils.filterByLob(sampleRecords(), "MISSING");
        assertTrue(result.isEmpty());
    }

    @Test
    void filterByLob_throwsOnNullLob() {
        ReportingUtils utils = new ReportingUtils();
        assertThrows(IllegalArgumentException.class,
            () -> utils.filterByLob(sampleRecords(), null));
    }

    private java.util.List<Record> sampleRecords() {
        return java.util.List.of(
            new Record("ENTERPRISE", 1),
            new Record("SMB", 2),
            new Record("ENTERPRISE", 3)
        );
    }
}
