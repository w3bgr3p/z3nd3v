import React, { useState, useEffect } from \"react\";
import \"../styles/SystemdPage.css\";

export default function SystemdPage() {
  const [services, setServices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedService, setSelectedService] = useState(null);
  const [serviceDetails, setServiceDetails] = useState(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [filter, setFilter] = useState(\"\");
  const [statusFilter, setStatusFilter] = useState(\"all\");

  useEffect(() => {
    loadServices();
    const interval = setInterval(loadServices, 5000);
    return () => clearInterval(interval);
  }, []);

  async function loadServices() {
    try {
      const res = await fetch(\"/systemd/list\");
      const data = await res.json();
      setServices(data.services || []);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function loadServiceDetails(serviceName) {
    setDetailsLoading(true);
    try {
      const [statusRes, logsRes] = await Promise.all([
        fetch(`/systemd/status/${encodeURIComponent(serviceName)}`),
        fetch(`/systemd/logs/${encodeURIComponent(serviceName)}?lines=50`)
      ]);
      
      const statusData = await statusRes.json();
      const logsData = await logsRes.json();
      
      setServiceDetails({
        ...statusData,
        logs: logsData.logs
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setDetailsLoading(false);
    }
  }

  async function handleAction(serviceName, action) {
    try {
      const res = await fetch(`/systemd/${action}/${encodeURIComponent(serviceName)}`, {
        method: \"POST\"
      });
      
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || \"Action failed\");
      }
      
      await loadServices();
      if (selectedService === serviceName) {
        await loadServiceDetails(serviceName);
      }
    } catch (err) {
      alert(`Failed to ${action} service: ${err.message}`);
    }
  }

  function openModal(service) {
    setSelectedService(service.unit);
    loadServiceDetails(service.unit);
  }

  function closeModal() {
    setSelectedService(null);
    setServiceDetails(null);
  }

  const filteredServices = services.filter(s => {
    const matchesName = s.unit?.toLowerCase().includes(filter.toLowerCase()) ||
                       s.description?.toLowerCase().includes(filter.toLowerCase());
    const matchesStatus = statusFilter === \"all\" || 
                         (statusFilter === \"active\" && s.active === \"active\") ||
                         (statusFilter === \"failed\" && s.active === \"failed\") ||
                         (statusFilter === \"inactive\" && s.active === \"inactive\");
    return matchesName && matchesStatus;
  });

  if (loading) {
    return <div className=\"systemd-page\"><div className=\"loading\">Loading services...</div></div>;
  }

  if (error) {
    return <div className=\"systemd-page\"><div className=\"error\">Error: {error}</div></div>;
  }

  return (
    <div className=\"systemd-page\">
      <div className=\"systemd-header\">
        <h1>Systemd Services</h1>
        <div className=\"systemd-controls\">
          <input
            type=\"text\"
            placeholder=\"Filter services...\"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className=\"filter-input\"
          />
          <select 
            value={statusFilter} 
            onChange={(e) => setStatusFilter(e.target.value)}
            className=\"status-filter\"
          >
            <option value=\"all\">All</option>
            <option value=\"active\">Active</option>
            <option value=\"failed\">Failed</option>
            <option value=\"inactive\">Inactive</option>
          </select>
          <button onClick={loadServices} className=\"refresh-btn\">Refresh</button>
        </div>
      </div>

      <div className=\"services-grid\">
        {filteredServices.map((service) => (
          <div
            key={service.unit}
            className={`service-card status-${service.active} sub-${service.sub}`}
            onClick={() => openModal(service)}
          >
            <div className=\"service-header\">
              <span className=\"service-name\">{service.unit}</span>
              <span className={`status-badge status-${service.active}`}>
                {service.active}
              </span>
            </div>
            <div className=\"service-description\">{service.description}</div>
            <div className=\"service-meta\">
              <span className=\"load-state\">{service.load}</span>
              <span className=\"sub-state\">{service.sub}</span>
            </div>
          </div>
        ))}
      </div>

      {selectedService && (
        <div className=\"modal-overlay\" onClick={closeModal}>
          <div className=\"modal-content\" onClick={(e) => e.stopPropagation()}>
            <div className=\"modal-header\">
              <h2>{selectedService}</h2>
              <button className=\"close-btn\" onClick={closeModal}>×</button>
            </div>

            {detailsLoading ? (
              <div className=\"loading\">Loading details...</div>
            ) : serviceDetails ? (
              <div className=\"modal-body\">
                <div className=\"action-buttons\">
                  <button onClick={() => handleAction(selectedService, \"start\")} className=\"btn-start\">
                    Start
                  </button>
                  <button onClick={() => handleAction(selectedService, \"stop\")} className=\"btn-stop\">
                    Stop
                  </button>
                  <button onClick={() => handleAction(selectedService, \"restart\")} className=\"btn-restart\">
                    Restart
                  </button>
                  <button onClick={() => handleAction(selectedService, \"enable\")} className=\"btn-enable\">
                    Enable
                  </button>
                  <button onClick={() => handleAction(selectedService, \"disable\")} className=\"btn-disable\">
                    Disable
                  </button>
                </div>

                <div className=\"details-section\">
                  <h3>Status</h3>
                  <pre className=\"status-output\">{serviceDetails.status_text}</pre>
                </div>

                <div className=\"details-section\">
                  <h3>Properties</h3>
                  <div className=\"properties-grid\">
                    {Object.entries(serviceDetails.properties || {})
                      .filter(([key]) => [
                        \"Id\", \"Description\", \"LoadState\", \"ActiveState\", \"SubState\",
                        \"MainPID\", \"ExecStart\", \"ExecMainStartTimestamp\", \"MemoryCurrent\",
                        \"CPUUsageNSec\", \"TasksCurrent\", \"Requires\", \"After\", \"Before\"
                      ].includes(key))
                      .map(([key, value]) => (
                        <div key={key} className=\"property-row\">
                          <span className=\"property-key\">{key}:</span>
                          <span className=\"property-value\">{value}</span>
                        </div>
                      ))}
                  </div>
                </div>

                <div className=\"details-section\">
                  <h3>Recent Logs (50 lines)</h3>
                  <pre className=\"logs-output\">{serviceDetails.logs}</pre>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

