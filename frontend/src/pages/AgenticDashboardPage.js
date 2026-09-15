import React, { useState, useEffect } from 'react';
import { agenticApi } from '../api';
import { 
  Card, Button, Table, Tag, Tabs, Select, Input, Modal, Form, message, 
  Space, Tooltip, Dropdown, Menu, Badge, Alert, Divider, Typography,
  DatePicker, TimePicker, Popconfirm, Avatar
} from 'antd';
import { 
  PlusOutlined, EditOutlined, DeleteOutlined, PlayCircleOutlined, 
  HistoryOutlined, ReloadOutlined, SearchOutlined, FilterOutlined,
  FileTextOutlined, WarningOutlined, CheckCircleOutlined, ClockCircleOutlined,
  ExclamationCircleOutlined, DownOutlined, UpOutlined, SettingOutlined
} from '@ant-design/icons';

const { Title, Text } = Typography;
const { Option } = Select;
const { RangePicker } = DatePicker;

const AgenticDashboardPage = () => {
  const [activeTab, setActiveTab] = useState('agents');
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState({ 
    agents: [], 
    risks: [], 
    playbooks: [], 
    intakes: [], 
    obligations: [], 
    alerts: [] 
  });

  useEffect(() => { loadAll(); }, [activeTab]);

  const loadAll = async () => {
    setLoading(true);
    try {
      const [agents, risks, playbooks, intakes, obligations, alerts] = await Promise.all([
        agenticApi.listAgents(),
        agenticApi.listRiskAssessments({ limit: 20 }),
        agenticApi.listPlaybooks(),
        agenticApi.listIntakes(),
        agenticApi.listObligations({ limit: 50 }),
        agenticApi.listObligationAlerts({ acknowledged: false }),
      ]);
      setData({ 
        agents: agents.data, 
        risks: risks.data, 
        playbooks: playbooks.data, 
        intakes: intakes.data, 
        obligations: obligations.data, 
        alerts: alerts.data 
      });
    } catch (e) { 
      message.error('Failed to load agentic data'); 
      console.error(e);
    }
    finally { setLoading(false); }
  };

  const getRiskColor = (level) => {
    const colors = { critical: 'red', high: 'orange', medium: 'gold', low: 'blue', minimal: 'green' };
    return colors[level] || 'default';
  };

  const getStatusColor = (status) => {
    const colors = { active: 'green', paused: 'gold', error: 'red', completed: 'blue' };
    return colors[status] || 'default';
  };

  const getIntakeStatusColor = (status) => {
    const colors = { received: 'blue', processing: 'gold', classified: 'purple', routed: 'green', failed: 'red', manual_review: 'orange' };
    return colors[status] || 'default';
  };

  const getObligationStatusColor = (status) => {
    const colors = { pending: 'blue', in_progress: 'gold', completed: 'green', overdue: 'red', waived: 'gray', disputed: 'orange' };
    return colors[status] || 'default';
  };

  // ===========================================
  // AGENTS TAB
  // ===========================================
  const [agentModalVisible, setAgentModalVisible] = useState(false);
  const [editingAgent, setEditingAgent] = useState(null);
  const [agentForm] = Form.useForm();

  const agentColumns = [
    { title: 'Name', dataIndex: 'name', key: 'name', width: 200 },
    { title: 'Type', dataIndex: 'agentType', key: 'agentType', width: 150, render: v => <Tag>{v}</Tag> },
    { title: 'Status', dataIndex: 'status', key: 'status', width: 100, render: v => <Tag color={getStatusColor(v)}>{v}</Tag> },
    { title: 'Schedule', dataIndex: 'schedule', key: 'schedule', width: 120, render: v => <Code>{v}</Code> },
    { title: 'Last Run', dataIndex: 'lastRunAt', key: 'lastRunAt', width: 160, render: v => v ? new Date(v).toLocaleString() : 'Never' },
    { title: 'Total Runs', dataIndex: 'totalExecutions', key: 'totalExecutions', width: 100 },
    { title: 'Alerts', dataIndex: 'totalAlertsGenerated', key: 'totalAlertsGenerated', width: 80 },
    { 
      title: 'Actions', 
      key: 'actions', 
      width: 200,
      render: (_, record) => (
        <Space>
          <Button type="link" icon={<PlayCircleOutlined />} onClick={() => handleRunAgent(record.id)}>Run</Button>
          <Button type="link" icon={<EditOutlined />} onClick={() => handleEditAgent(record)}>Edit</Button>
          <Button type="link" icon={<HistoryOutlined />} onClick={() => handleViewExecutions(record.id)}>History</Button>
          <Popconfirm title="Delete agent?" onConfirm={() => handleDeleteAgent(record.id)} okText="Yes" cancelText="No">
            <Button type="link" danger icon={<DeleteOutlined />}>Delete</Button>
          </Popconfirm>
        </Space>
      )
    },
  ];

  const handleRunAgent = async (agentId) => {
    try {
      await agenticApi.runAgent(agentId);
      message.success('Agent run triggered');
      loadAll();
    } catch (e) { message.error('Failed to run agent'); }
  };

  const handleEditAgent = (agent) => {
    setEditingAgent(agent);
    agentForm.setFieldsValue({
      name: agent.name,
      description: agent.description || '',
      agentType: agent.agentType,
      schedule: agent.schedule,
      config: JSON.stringify(agent.config, null, 2),
    });
    setAgentModalVisible(true);
  };

  const handleCreateAgent = () => {
    setEditingAgent(null);
    agentForm.resetFields();
    agentForm.setFieldsValue({ agentType: 'expiration_monitor', schedule: '0 9 * * *' });
    setAgentModalVisible(true);
  };

  const handleDeleteAgent = async (agentId) => {
    try {
      await agenticApi.deleteAgent(agentId);
      message.success('Agent deleted');
      loadAll();
    } catch (e) { message.error('Failed to delete agent'); }
  };

  const handleViewExecutions = async (agentId) => {
    try {
      const res = await agenticApi.getAgentExecutions(agentId);
      // Show in modal or navigate
      console.log('Executions:', res.data);
      message.info('Check console for execution history');
    } catch (e) { message.error('Failed to load executions'); }
  };

  const handleAgentSubmit = async (values) => {
    try {
      const config = values.config ? JSON.parse(values.config) : {};
      const payload = { ...values, config };
      if (editingAgent) {
        await agenticApi.updateAgent(editingAgent.id, payload);
        message.success('Agent updated');
      } else {
        await agenticApi.createAgent(payload);
        message.success('Agent created');
      }
      setAgentModalVisible(false);
      loadAll();
    } catch (e) { message.error('Failed to save agent'); console.error(e); }
  };

  // ===========================================
  // RISKS TAB
  // ===========================================
  const riskColumns = [
    { title: 'Contract', dataIndex: 'contractId', key: 'contractId', width: 150, render: v => <Text ellipsis={{ rows: 1 }}>{v}</Text> },
    { title: 'Score', dataIndex: 'overall_score', key: 'overall_score', width: 80, sorter: (a,b) => b.overall_score - a.overall_score, render: v => <strong>{v}/100</strong> },
    { title: 'Risk Level', dataIndex: 'risk_level', key: 'risk_level', width: 120, render: v => <Tag color={getRiskColor(v)}>{v}</Tag> },
    { title: 'Trend', dataIndex: 'score_trend', key: 'score_trend', width: 100, render: v => {
      const icons = { improving: <UpOutlined style={{color:'green'}}/>, stable: <MinusOutlined style={{color:'gray'}}/>, degrading: <DownOutlined style={{color:'red'}}/>, '-' : null };
      return <span>{icons[v] || ''} {v}</span>;
    }},
    { title: 'Breakdown', dataIndex: 'breakdown', key: 'breakdown', width: 200, render: v => (
      <Space direction="vertical" size={2}>
        {Object.entries(v).filter(([k]) => k !== 'weighted_scores').map(([cat, score]) => 
          <Text key={cat}><strong>{cat}:</strong> {score}</Text>
        )}
      </Space>
    )},
    { title: 'Assessed', dataIndex: 'assessed_at', key: 'assessed_at', width: 160, render: v => v ? new Date(v).toLocaleString() : '-' },
    { 
      title: 'Actions', 
      key: 'actions', 
      width: 120,
      render: (_, record) => (
        <Button type="link" onClick={() => handleRefreshRisk(record.contractId)}>Refresh</Button>
      )
    },
  ];

  const handleRefreshRisk = async (contractId) => {
    try {
      await agenticApi.assessRisk(contractId, true);
      message.success('Risk assessment queued');
      loadAll();
    } catch (e) { message.error('Failed to queue assessment'); }
  };

  const handleBulkAssess = async () => {
    try {
      await agenticApi.bulkAssessRisks();
      message.success('Bulk risk assessment queued');
    } catch (e) { message.error('Failed to queue bulk assessment'); }
  };

  const handleRefreshStale = async () => {
    try {
      await agenticApi.refreshStaleRisks();
      message.success('Stale risk refresh queued');
    } catch (e) { message.error('Failed to queue stale refresh'); }
  };

  // ===========================================
  // PLAYBOOKS TAB
  // ===========================================
  const [playbookModalVisible, setPlaybookModalVisible] = useState(false);
  const [editingPlaybook, setEditingPlaybook] = useState(null);
  const [playbookForm] = Form.useForm();
  const [playbookRules, setPlaybookRules] = useState([]);

  const playbookColumns = [
    { title: 'Name', dataIndex: 'name', key: 'name', width: 200 },
    { title: 'Version', dataIndex: 'version', key: 'version', width: 80 },
    { title: 'Rules', dataIndex: 'rules_count', key: 'rules_count', width: 80 },
    { title: 'Contract Types', dataIndex: 'contract_types', key: 'contract_types', width: 200, render: v => v.join(', ') || 'All' },
    { title: 'Jurisdictions', dataIndex: 'jurisdictions', key: 'jurisdictions', width: 150, render: v => v.join(', ') || 'All' },
    { title: 'Default', dataIndex: 'is_default', key: 'is_default', width: 80, render: v => v ? <Tag color="green">Yes</Tag> : <Tag>No</Tag> },
    { title: 'Active', dataIndex: 'is_active', key: 'is_active', width: 80, render: v => v ? <Tag color="green">Yes</Tag> : <Tag color="red">No</Tag> },
    { 
      title: 'Actions', 
      key: 'actions', 
      width: 200,
      render: (_, record) => (
        <Space>
          <Button type="link" icon={<FileTextOutlined />} onClick={() => handleAnalyzeWithPlaybook(record.id)}>Analyze Contract</Button>
          <Button type="link" icon={<EditOutlined />} onClick={() => handleEditPlaybook(record)}>Edit</Button>
          <Popconfirm title="Delete playbook?" onConfirm={() => handleDeletePlaybook(record.id)} okText="Yes" cancelText="No">
            <Button type="link" danger icon={<DeleteOutlined />}>Delete</Button>
          </Popconfirm>
        </Space>
      )
    },
  ];

  const handleAnalyzeWithPlaybook = (playbookId) => {
    message.info('Select a contract to analyze from the Contracts page, then use "Analyze with Playbook" action');
  };

  const handleEditPlaybook = (playbook) => {
    setEditingPlaybook(playbook);
    playbookForm.setFieldsValue({
      name: playbook.name,
      description: playbook.description || '',
      contract_types: playbook.contract_types.join(','),
      jurisdictions: playbook.jurisdictions.join(','),
      counterparty_types: playbook.counterparty_types.join(','),
      auto_apply: playbook.auto_apply,
      require_approval: playbook.require_approval,
      is_default: playbook.is_default,
    });
    setPlaybookRules(playbook.rules || []);
    setPlaybookModalVisible(true);
  };

  const handleCreatePlaybook = () => {
    setEditingPlaybook(null);
    playbookForm.resetFields();
    playbookForm.setFieldsValue({ auto_apply: false, require_approval: true });
    setPlaybookRules([]);
    setPlaybookModalVisible(true);
  };

  const handleDeletePlaybook = async (playbookId) => {
    try {
      await agenticApi.deletePlaybook(playbookId);
      message.success('Playbook deleted');
      loadAll();
    } catch (e) { message.error('Failed to delete playbook'); }
  };

  const handlePlaybookSubmit = async (values) => {
    try {
      const rules = playbookRules.map(r => ({ ...r, id: r.id || `rule-${Date.now()}-${Math.random()}` }));
      const payload = {
        ...values,
        contract_types: values.contract_types.split(',').filter(Boolean),
        jurisdictions: values.jurisdictions.split(',').filter(Boolean),
        counterparty_types: values.counterparty_types.split(',').filter(Boolean),
        rules,
      };
      if (editingPlaybook) {
        await agenticApi.updatePlaybook(editingPlaybook.id, payload);
        message.success('Playbook updated');
      } else {
        await agenticApi.createPlaybook(payload);
        message.success('Playbook created');
      }
      setPlaybookModalVisible(false);
      loadAll();
    } catch (e) { message.error('Failed to save playbook'); console.error(e); }
  };

  // ===========================================
  // INTAKE TAB
  // ===========================================
  const intakeColumns = [
    { title: 'Sender', dataIndex: 'source_email', key: 'source_email', width: 200 },
    { title: 'Subject', dataIndex: 'source_metadata.subject', key: 'subject', width: 250, render: v => <Text ellipsis={{ rows: 1 }}>{v || 'N/A'}</Text> },
    { title: 'Status', dataIndex: 'status', key: 'status', width: 120, render: v => <Tag color={getIntakeStatusColor(v)}>{v}</Tag> },
    { title: 'Classified Type', dataIndex: 'classified_type', key: 'classified_type', width: 150 },
    { title: 'Counterparty', dataIndex: 'classified_counterparty', key: 'classified_counterparty', width: 150 },
    { title: 'Confidence', dataIndex: 'confidence', key: 'confidence', width: 100, render: v => `${Math.round(v * 100)}%` },
    { title: 'Attachments', dataIndex: 'attachments', key: 'attachments', width: 100, render: v => v?.length || 0 },
    { title: 'Created Contract', dataIndex: 'created_contract_id', key: 'created_contract_id', width: 150, render: v => v ? <a href={`/contracts/${v}`}>{v}</a> : '-' },
    { title: 'Received', dataIndex: 'received_at', key: 'received_at', width: 160, render: v => v ? new Date(v).toLocaleString() : '-' },
    { 
      title: 'Actions', 
      key: 'actions', 
      width: 150,
      render: (_, record) => (
        <Space>
          {record.status !== 'routed' && record.status !== 'failed' && (
            <Button type="link" onClick={() => handleProcessIntake(record.id)}>Process</Button>
          )}
          <Button type="link" onClick={() => handleViewIntake(record.id)}>View</Button>
        </Space>
      )
    },
  ];

  const handleProcessIntake = async (intakeId) => {
    try {
      await agenticApi.processIntake(intakeId, {});
      message.success('Intake processing started');
      loadAll();
    } catch (e) { message.error('Failed to process intake'); }
  };

  const handleViewIntake = (intakeId) => {
    message.info('View intake details - implement modal');
  };

  const handleRetryFailed = async () => {
    try {
      await agenticApi.retryFailedIntakes();
      message.success('Failed intakes retry queued');
      loadAll();
    } catch (e) { message.error('Failed to retry'); }
  };

  // ===========================================
  // OBLIGATIONS TAB
  // ===========================================
  const [obligationModalVisible, setObligationModalVisible] = useState(false);
  const [selectedObligation, setSelectedObligation] = useState(null);
  const [obligationStatusForm] = Form.useForm();
  const [statusFilter, setStatusFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [assignedToMe, setAssignedToMe] = useState(false);

  const obligationColumns = [
    { title: 'Title', dataIndex: 'title', key: 'title', width: 200, render: v => <Text ellipsis>{{v}}</Text> },
    { title: 'Type', dataIndex: 'obligation_type', key: 'obligation_type', width: 120, render: v => <Tag>{v}</Tag> },
    { title: 'Contract', dataIndex: 'contractId', key: 'contractId', width: 150, render: v => <Text ellipsis>{{v}}</Text> },
    { title: 'Party', dataIndex: 'obligated_party', key: 'obligated_party', width: 100, render: v => v === 'our_party' ? <Tag color="blue">Us</Tag> : <Tag color="orange">Them</Tag> },
    { title: 'Due Date', dataIndex: 'due_date', key: 'due_date', width: 120, render: v => v ? (
      <span style={{ color: new Date(v) < new Date() ? 'red' : 'inherit' }}>
        {new Date(v).toLocaleDateString()} {new Date(v) < new Date() ? '⚠ OVERDUE' : ''}
      </span>
    ) : '—' },
    { title: 'Frequency', dataIndex: 'frequency', key: 'frequency', width: 100 },
    { title: 'Status', dataIndex: 'status', key: 'status', width: 120, render: v => <Tag color={getObligationStatusColor(v)}>{v}</Tag> },
    { title: 'Assigned To', dataIndex: 'assigned_to', key: 'assigned_to', width: 120 },
    { title: 'Amount', dataIndex: 'amount', key: 'amount', width: 100, render: v => v ? `${v} ${obl => obl.currency || 'USD'}` : '-' },
    { 
      title: 'Actions', 
      key: 'actions', 
      width: 150,
      render: (_, record) => (
        <Space>
          <Button type="link" onClick={() => handleUpdateObligationStatus(record)}>Update Status</Button>
          {record.evidence_required && (
            <Button type="link" onClick={() => handleAddEvidence(record)}>Add Evidence</Button>
          )}
        </Space>
      )
    },
  ];

  const filteredObligations = data.obligations.filter(o => {
    if (statusFilter && o.status !== statusFilter) return false;
    if (typeFilter && o.obligation_type !== typeFilter) return false;
    if (assignedToMe && o.assigned_to !== 'current-user-id') return false; // Would use actual user ID
    return true;
  });

  const handleUpdateObligationStatus = (obligation) => {
    setSelectedObligation(obligation);
    obligationStatusForm.setFieldsValue({ status: obligation.status, comment: '' });
    setObligationModalVisible(true);
  };

  const handleAddEvidence = (obligation) => {
    message.info('Add evidence - implement file upload');
  };

  const handleObligationStatusSubmit = async (values) => {
    try {
      await agenticApi.updateObligationStatus(selectedObligation.id, values);
      message.success('Obligation updated');
      setObligationModalVisible(false);
      loadAll();
    } catch (e) { message.error('Failed to update obligation'); }
  };

  const handleAcknowledgeAlert = async (alertId) => {
    try {
      await agenticApi.acknowledgeAlert(alertId);
      message.success('Alert acknowledged');
      loadAll();
    } catch (e) { message.error('Failed to acknowledge'); }
  };

  const handleBulkExtract = async () => {
    try {
      await agenticApi.bulkExtractObligations();
      message.success('Bulk obligation extraction queued');
    } catch (e) { message.error('Failed to queue extraction'); }
  };

  // ===========================================
  // RENDER
  // ===========================================
  const tabs = [
    { 
      key: 'agents', 
      tab: `Agents (${data.agents.length})`, 
      component: (
        <Card>
          <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
            <Title level={4}>Contract Agents</Title>
            <Button type="primary" icon={<PlusOutlined />} onClick={handleCreateAgent}>Create Agent</Button>
          </div>
          <Table columns={agentColumns} dataSource={data.agents} loading={loading} rowKey="id" pagination={{ pageSize: 10 }} />
        </Card>
      )
    },
    { 
      key: 'risks', 
      tab: `Risk Scores (${data.risks.length})`, 
      component: (
        <Card>
          <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
            <Title level={4}>Automated Risk Scoring</Title>
            <Space>
              <Button icon={<ReloadOutlined />} onClick={handleRefreshStale}>Refresh Stale</Button>
              <Button type="primary" icon={<ReloadOutlined />} onClick={handleBulkAssess}>Bulk Assess All</Button>
            </Space>
          </div>
          <Table columns={riskColumns} dataSource={data.risks} loading={loading} rowKey="id" pagination={{ pageSize: 10 }} defaultSortOrder="descend" defaultSortedColumns={[{ columnKey: 'overall_score', order: 'descend' }]} />
        </Card>
      )
    },
    { 
      key: 'playbooks', 
      tab: `Playbooks (${data.playbooks.length})`, 
      component: (
        <Card>
          <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
            <Title level={4}>Legal Playbooks</Title>
            <Button type="primary" icon={<PlusOutlined />} onClick={handleCreatePlaybook}>Create Playbook</Button>
          </div>
          <Table columns={playbookColumns} dataSource={data.playbooks} loading={loading} rowKey="id" pagination={{ pageSize: 10 }} />
        </Card>
      )
    },
    { 
      key: 'intake', 
      tab: `Intake (${data.intakes.length})`, 
      component: (
        <Card>
          <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
            <Title level={4}>Business Intake (Email)</Title>
            <Space>
              <Button icon={<ReloadOutlined />} onClick={handleRetryFailed}>Retry Failed</Button>
              <Alert message="Configure Resend webhook: POST /api/v1/agentic/intake/webhook/resend" type="info" showIcon />
            </Space>
          </div>
          <Table columns={intakeColumns} dataSource={data.intakes} loading={loading} rowKey="id" pagination={{ pageSize: 10 }} />
        </Card>
      )
    },
    { 
      key: 'obligations', 
      tab: `Obligations (${data.obligations.length})`, 
      component: (
        <Card>
          <div style={{ marginBottom: 16 }}>
            <Title level={4}>Post-Signature Obligations</Title>
            <Alert message={`Unacknowledged alerts: ${data.alerts.filter(a => !a.acknowledged).length} (${data.alerts.filter(a => a.severity === 'critical' && !a.acknowledged).length} critical)`} type="warning" showIcon style={{ marginBottom: 16 }} />
          </div>
          <div style={{ marginBottom: 16, display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <Select placeholder="Status" value={statusFilter} onChange={setStatusFilter} style={{ width: 150 }} options={['pending','in_progress','completed','overdue','waived','disputed'].map(v => ({value:v,label:v}))} allowClear />
            <Select placeholder="Type" value={typeFilter} onChange={setTypeFilter} style={{ width: 150 }} options={['payment','delivery','reporting','compliance','renewal','termination','insurance','indemnification','confidentiality'].map(v => ({value:v,label:v}))} allowClear />
            <Button onClick={handleBulkExtract} icon={<ReloadOutlined />}>Bulk Extract</Button>
          </div>
          <Table columns={obligationColumns} dataSource={filteredObligations} loading={loading} rowKey="id" pagination={{ pageSize: 15 }} />
          <Divider style={{ marginTop: 24 }} />
          <Title level={5}>Unacknowledged Alerts</Title>
          <Table 
            dataSource={data.alerts.filter(a => !a.acknowledged).slice(0, 10)} 
            rowKey="id" 
            pagination={false}
            columns={[
              { title: 'Obligation', dataIndex: 'message', key: 'message', width: 300 },
              { title: 'Severity', dataIndex: 'severity', key: 'severity', width: 100, render: v => <Tag color={v==='critical'?'red':v==='warning'?'orange':'blue'}>{v}</Tag> },
              { title: 'Sent', dataIndex: 'sent_at', key: 'sent_at', width: 160, render: v => v ? new Date(v).toLocaleString() : '-' },
              { title: 'Action', key: 'ack', width: 120, render: (_, record) => <Button type="link" size="small" onClick={() => handleAcknowledgeAlert(record.id)}>Acknowledge</Button> }
            ]}
          />
        </Card>
      )
    },
  ];

  return (
    <div className="page-container">
      <div className="page-header">
        <h1>Agentic AI Platform</h1>
        <p>Autonomous contract intelligence: agents, risk scoring, playbooks, intake & obligations</p>
      </div>
      <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabs} />
      
      {/* Agent Modal */}
      <Modal title={editingAgent ? 'Edit Agent' : 'Create Agent'} open={agentModalVisible} onCancel={() => setAgentModalVisible(false)} width={600} footer={null}>
        <Form form={agentForm} layout="vertical" onFinish={handleAgentSubmit}>
          <Form.Item name="name" label="Name" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="description" label="Description"><Input.TextArea rows={2} /></Form.Item>
          <Form.Item name="agentType" label="Agent Type" rules={[{ required: true }]}>
            <Select options={['expiration_monitor','compliance_checker','risk_alert','obligation_tracker','renewal_negotiator','custom'].map(v => ({value:v,label:v}))} />
          </Form.Item>
          <Form.Item name="schedule" label="Cron Schedule" rules={[{ required: true }]}><Input placeholder="0 9 * * *" /></Form.Item>
          <Form.Item name="config" label="Config (JSON)">
            <Input.TextArea rows={6} placeholder='{"check_interval_hours":24,"alert_threshold":30,"contract_types":[]}' />
          </Form.Item>
          <Space style={{ marginTop: 16, width: '100%', justifyContent: 'flex-end' }}>
            <Button htmlType="submit" type="primary">Save</Button>
            <Button onClick={() => setAgentModalVisible(false)}>Cancel</Button>
          </Space>
        </Form>
      </Modal>

      {/* Playbook Modal */}
      <Modal title={editingPlaybook ? 'Edit Playbook' : 'Create Playbook'} open={playbookModalVisible} onCancel={() => setPlaybookModalVisible(false)} width={800} footer={null}>
        <Form form={playbookForm} layout="vertical" onFinish={handlePlaybookSubmit}>
          <Form.Item name="name" label="Name" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="description" label="Description"><Input.TextArea rows={2} /></Form.Item>
          <Form.Item name="contract_types" label="Contract Types (comma-separated)"><Input placeholder="NDA,Service Agreement" /></Form.Item>
          <Form.Item name="jurisdictions" label="Jurisdictions (comma-separated)"><Input placeholder="US,UK,EU" /></Form.Item>
          <Form.Item name="counterparty_types" label="Counterparty Types (comma-separated)"><Input placeholder="vendor,customer,partner" /></Form.Item>
          <Form.Item name="auto_apply" label="Auto-apply redlines" valuePropName="checked"><Select valuePropName="checked"><Option value={true}>Yes</Option></Select></Form.Item>
          <Form.Item name="require_approval" label="Require approval" valuePropName="checked"><Select valuePropName="checked"><Option value={true}>Yes</Option></Select></Form.Item>
          <Form.Item name="is_default" label="Default playbook" valuePropName="checked"><Select valuePropName="checked"><Option value={true}>Yes</Option></Select></Form.Item>
          <Divider>Rules (edit in JSON)</Divider>
          <Form.Item name="rules_json" label="Rules JSON">
            <Input.TextArea rows={8} placeholder='[{"clause_name":"limitation_of_liability","clause_type":"standard","rule_type":"must_have","description":"Must have limitation clause","required":true,"priority":10}]' />
          </Form.Item>
          <Space style={{ marginTop: 16, width: '100%', justifyContent: 'flex-end' }}>
            <Button htmlType="submit" type="primary">Save</Button>
            <Button onClick={() => setPlaybookModalVisible(false)}>Cancel</Button>
          </Space>
        </Form>
      </Modal>

      {/* Obligation Status Modal */}
      <Modal title="Update Obligation Status" open={obligationModalVisible} onCancel={() => setObligationModalVisible(false)} width={500} footer={null}>
        <Form form={obligationStatusForm} layout="vertical" onFinish={handleObligationStatusSubmit}>
          <Form.Item name="status" label="Status" rules={[{ required: true }]}>
            <Select options={['pending','in_progress','completed','overdue','waived','disputed'].map(v => ({value:v,label:v}))} />
          </Form.Item>
          <Form.Item name="comment" label="Comment"><Input.TextArea rows={3} /></Form.Item>
          <Space style={{ marginTop: 16, width: '100%', justifyContent: 'flex-end' }}>
            <Button htmlType="submit" type="primary">Save</Button>
            <Button onClick={() => setObligationModalVisible(false)}>Cancel</Button>
          </Space>
        </Form>
      </Modal>
    </div>
  );
};

export default AgenticDashboardPage;