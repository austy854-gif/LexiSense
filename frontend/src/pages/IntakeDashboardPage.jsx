import { useState, useEffect, useCallback } from 'react';
import { agenticApi } from '../api';
import { Layout } from '../components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../components/ui/tabs';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell, TableCaption } from '../components/ui/table';
import { Progress } from '../components/ui/progress';
import { Avatar, AvatarFallback } from '../components/ui/avatar';
import { ScrollArea } from '../components/ui/scroll-area';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator } from '../components/ui/dropdown-menu';
import { Modal } from '../components/ui/dialog';
import { Form, FormField, FormItem, FormLabel, FormControl, FormDescription, FormMessage } from '../components/ui/form';
import { Checkbox } from '../components/ui/checkbox';
import { Label } from '../components/ui/label';
import { Separator } from '../components/ui/separator';
import { Descriptions, DescriptionsItem } from '../components/ui/descriptions';
import { Alert } from '../components/ui/alert';
import { Skeleton } from '../components/ui/skeleton';
import { Empty } from '../components/ui/empty';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '../components/ui/tooltip';
import { Popover, PopoverContent, PopoverTrigger } from '../components/ui/popover';
import {
  Mail,
  FileText,
  Search,
  Filter,
  Edit,
  Eye,
  Download,
  CheckCircle,
  Clock,
  AlertCircle,
  ArrowRight,
  LeftOutlined,
  Settings,
  RefreshCw,
  Users,
  Paperclip,
  Database,
  Bot,
  Copy,
  ChevronDown,
  ChevronRight,
  File,
  Link as LinkIcon,
  Send,
  Shield,
  Zap,
} from 'lucide-react';
import { useToast } from '../hooks/use-toast';

const statusColors = {
  received: 'bg-blue-100 text-blue-800 border-blue-200',
  processing: 'bg-yellow-100 text-yellow-800 border-yellow-200',
  classified: 'bg-purple-100 text-purple-800 border-purple-200',
  routed: 'bg-green-100 text-green-800 border-green-200',
  failed: 'bg-red-100 text-red-800 border-red-200',
  manual_review: 'bg-orange-100 text-orange-800 border-orange-200',
};

const statusLabels = {
  received: 'Received',
  processing: 'Processing',
  classified: 'Classified',
  routed: 'Routed',
  failed: 'Failed',
  manual_review: 'Manual Review',
};

const intakeStatusSteps = [
  { key: 'received', title: 'Received', description: 'Email received via Resend webhook' },
  { key: 'processing', title: 'Processing', description: 'Attachments extracted & stored' },
  { key: 'classified', title: 'Classified', description: 'AI classified contract type & counterparty' },
  { key: 'routed', title: 'Routed', description: 'Assigned to team & playbook selected' },
  { key: 'contract_created', title: 'Contract Created', description: 'Contract record created in system' },
];

const StatCard = ({ title, value, icon: Icon, color = 'primary' }) => (
  <Card className="hover:shadow-md transition-shadow">
    <CardContent className="p-6">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm font-medium text-muted-foreground">{title}</p>
          <p className="text-3xl font-bold mt-2">{value}</p>
        </div>
        <div className={`p-3 rounded-lg bg-${color}/10`}>
          <Icon className={`h-6 w-6 text-${color}`} />
        </div>
      </div>
    </CardContent>
  </Card>
);

export default function IntakeDashboardPage() {
  const [loading, setLoading] = useState(false);
  const [intakes, setIntakes] = useState([]);
  const [selectedIntake, setSelectedIntake] = useState(null);
  const [detailModalOpen, setDetailModalOpen] = useState(false);
  const [processModalOpen, setProcessModalOpen] = useState(false);
  const [configModalOpen, setConfigModalOpen] = useState(false);
  const [playbooks, setPlaybooks] = useState([]);
  const [teamMembers, setTeamMembers] = useState([]);
  const [filters, setFilters] = useState({
    status: '',
    dateRange: [],
    search: '',
  });
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20, total: 0 });
  const [webhookConfig, setWebhookConfig] = useState(null);
  const { toast } = useToast();

  const fetchIntakes = useCallback(async () => {
    setLoading(true);
    try {
      const params = {
        status: filters.status,
        limit: pagination.pageSize,
        skip: (pagination.current - 1) * pagination.pageSize,
      };
      
      if (filters.dateRange && filters.dateRange.length === 2) {
        params.received_after = filters.dateRange[0].toISOString();
        params.received_before = filters.dateRange[1].toISOString();
      }
      
      if (filters.search) {
        params.search = filters.search;
      }
      
      const res = await agenticApi.listIntakes(params);
      const data = res.data || [];
      setIntakes(data);
      setPagination(prev => ({ ...prev, total: res.total || data.length }));
    } catch (e) {
      toast({ title: 'Error', description: 'Failed to load intakes', variant: 'destructive' });
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [filters, pagination.current, pagination.pageSize, toast]);

  const fetchPlaybooks = useCallback(async () => {
    try {
      const res = await agenticApi.listPlaybooks();
      setPlaybooks(res.data || []);
    } catch (e) {
      console.error('Failed to load playbooks:', e);
    }
  }, []);

  const fetchTeamMembers = useCallback(async () => {
    try {
      const token = localStorage.getItem('token');
      const res = await fetch('/api/team/members', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        setTeamMembers(data.data || []);
      }
    } catch (e) {
      console.error('Failed to load team members:', e);
    }
  }, []);

  const fetchWebhookConfig = useCallback(async () => {
    try {
      const token = localStorage.getItem('token');
      const res = await fetch('/api/agentic/intake/config', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        setWebhookConfig(data);
      }
    } catch (e) {
      console.error('Failed to load webhook config:', e);
    }
  }, []);

  useEffect(() => {
    fetchIntakes();
    fetchPlaybooks();
    fetchTeamMembers();
    fetchWebhookConfig();
  }, [fetchIntakes, fetchPlaybooks, fetchTeamMembers, fetchWebhookConfig]);

  const handleSearch = (value) => {
    setFilters(prev => ({ ...prev, search: value }));
    setPagination(prev => ({ ...prev, current: 1 }));
  };

  const handleStatusFilter = (value) => {
    setFilters(prev => ({ ...prev, status: value }));
    setPagination(prev => ({ ...prev, current: 1 }));
  };

  const handleDateRangeChange = (dates) => {
    setFilters(prev => ({ ...prev, dateRange: dates }));
    setPagination(prev => ({ ...prev, current: 1 }));
  };

  const handleViewIntake = (intake) => {
    setSelectedIntake(intake);
    setDetailModalOpen(true);
  };

  const handleProcessIntake = (intake) => {
    setSelectedIntake(intake);
    setProcessModalOpen(true);
  };

  const handleCreateContract = async (intakeId) => {
    try {
      await agenticApi.processIntake(intakeId, { 
        assign_to: 'auto',
        playbook_id: 'auto'
      });
      toast({ title: 'Success', description: 'Contract creation initiated' });
      fetchIntakes();
    } catch (e) {
      toast({ title: 'Error', description: 'Failed to create contract', variant: 'destructive' });
    }
  };

  const handleProcessSubmit = async (values) => {
    try {
      await agenticApi.processIntake(selectedIntake.id, values);
      toast({ title: 'Success', description: 'Intake processed successfully' });
      setProcessModalOpen(false);
      fetchIntakes();
    } catch (e) {
      toast({ title: 'Error', description: 'Failed to process intake', variant: 'destructive' });
    }
  };

  const handleRetryFailed = async () => {
    try {
      await agenticApi.retryFailedIntakes();
      toast({ title: 'Success', description: 'Failed intakes retry queued' });
      fetchIntakes();
    } catch (e) {
      toast({ title: 'Error', description: 'Failed to retry', variant: 'destructive' });
    }
  };

  const getCurrentStepIndex = (status) => {
    const statusOrder = ['received', 'processing', 'classified', 'routed'];
    return statusOrder.indexOf(status);
  };

  const renderStatusBadge = (status) => (
    <Badge variant="outline" className={statusColors[status] || 'bg-gray-100 text-gray-800 border-gray-200'}>
      {statusLabels[status] || status}
    </Badge>
  );

  const renderAttachments = (attachments) => {
    if (!attachments || attachments.length === 0) return <span className="text-muted-foreground">—</span>;
    return (
      <div className="space-y-1">
        {attachments.map((att, idx) => (
          <div key={idx} className="flex items-center gap-2 text-sm">
            <Paperclip className="h-3 w-3 text-muted-foreground" />
            <span className="truncate max-w-[150px]">{att.filename}</span>
            <Badge variant="secondary" className="text-xs">{Math.round(att.size / 1024)} KB</Badge>
          </div>
        ))}
      </div>
    );
  };

  const getPlaybookName = (playbookId) => {
    if (!playbookId) return '—';
    const pb = playbooks.find(p => p.id === playbookId);
    return pb ? pb.name : playbookId;
  };

  // Detail Modal
  const DetailModal = () => {
    if (!selectedIntake) return null;
    
    const intake = selectedIntake;
    const currentStep = getCurrentStepIndex(intake.status);
    
    return (
      <Modal open={detailModalOpen} onOpenChange={setDetailModalOpen} className="max-w-4xl max-h-[90vh]">
        <div className="flex flex-col h-full">
          <div className="flex items-center justify-between border-b p-4">
            <div>
              <Modal.Title className="text-lg font-semibold">
                {intake.source_metadata?.subject || 'No Subject'}
              </Modal.Title>
              <p className="text-sm text-muted-foreground">{intake.source_email}</p>
            </div>
            <Button variant="ghost" size="icon" onClick={() => setDetailModalOpen(false)}>
              <X className="h-4 w-4" />
            </Button>
          </div>
          
          <div className="flex-1 overflow-y-auto p-4 space-y-6">
            {/* Status Progress */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Processing Pipeline</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  {intakeStatusSteps.map((step, idx) => (
                    <div key={step.key} className="flex items-start gap-4">
                      <div className="flex flex-col items-center">
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium ${
                          idx < currentStep ? 'bg-green-500 text-white' :
                          idx === currentStep ? 'bg-primary text-white' :
                          'bg-muted text-muted-foreground'
                        }`}>
                          {idx < currentStep ? <CheckCircle className="h-4 w-4" /> : idx + 1}
                        </div>
                        {idx < intakeStatusSteps.length - 1 && (
                          <div className={`w-0.5 h-8 ${idx < currentStep ? 'bg-green-500' : 'bg-muted'}`} />
                        )}
                      </div>
                      <div className="flex-1">
                        <p className="font-medium">{step.title}</p>
                        <p className="text-sm text-muted-foreground">{step.description}</p>
                      </div>
                    </div>
                  ))}
                  <div className="flex items-start gap-4">
                    <div className="flex flex-col items-center">
                      <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium ${
                        intake.created_contract_id ? 'bg-green-500 text-white' : 'bg-muted text-muted-foreground'
                      }`}>
                        {intake.created_contract_id ? <CheckCircle className="h-4 w-4" /> : '6'}
                      </div>
                    </div>
                    <div className="flex-1">
                      <p className="font-medium">Contract Created</p>
                      <p className="text-sm text-muted-foreground">Contract record created & workflow started</p>
                    </div>
                  </div>
                </div>
              </CardContent            </Card>

            <Tabs defaultValue="overview" className="space-y-4">
              <TabsList>
                <TabsTrigger value="overview">Overview</TabsTrigger>
                <TabsTrigger value="source">Raw Source</TabsTrigger>
                <TabsTrigger value="history">History</TabsTrigger>
              </TabsList>
              
              <TabsContent value="overview" className="space-y-6">
                <div className="space-y-6">
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-base">Basic Information</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <Descriptions className="space-y-3">
                        <DescriptionsItem label="Intake ID">{intake.id}</DescriptionsItem>
                        <DescriptionsItem label="Channel">
                          <Badge variant="outline">{intake.channel}</Badge>
                        </DescriptionsItem>
                        <DescriptionsItem label="Sender">{intake.source_email}</DescriptionsItem>
                        <DescriptionsItem label="Subject">{intake.source_metadata?.subject || 'N/A'}</DescriptionsItem>
                        <DescriptionsItem label="Received">{intake.received_at ? new Date(intake.received_at).toLocaleString() : 'N/A'}</DescriptionsItem>
                        <DescriptionsItem label="Processing Started">{intake.processing_started_at ? new Date(intake.processing_started_at).toLocaleString() : 'N/A'}</DescriptionsItem>
                        <DescriptionsItem label="Processing Completed">{intake.processing_completed_at ? new Date(intake.processing_completed_at).toLocaleString() : 'N/A'}</DescriptionsItem>
                        <DescriptionsItem label="Error">{intake.error_message || 'None'}</DescriptionsItem>
                      </Descriptions>
                    </CardContent>
                  </Card>
                  
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-base">AI Classification</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <Descriptions className="space-y-3">
                        <DescriptionsItem label="Contract Type">
                          {intake.classified_type ? (
                            <Badge variant="secondary" className="bg-blue-100 text-blue-800">{intake.classified_type}</Badge>
                          ) : 'Not classified'}
                        </DescriptionsItem>
                        <DescriptionsItem label="Counterparty">{intake.classified_counterparty || 'Not identified'}</DescriptionsItem>
                        <DescriptionsItem label="Confidence">
                          <div className="flex items-center gap-3">
                            <Progress value={Math.round((intake.confidence || 0) * 100)} className="flex-1 max-w-[200px]" />
                            <span className="font-medium">{Math.round((intake.confidence || 0) * 100)}%</span>
                          </div>
                        </DescriptionsItem>
                        <DescriptionsItem label="Reasoning">{intake.classification_reasoning || 'N/A'}</DescriptionsItem>
                      </Descriptions>
                    </CardContent>
                  </Card>
                  
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-base">Routing Information</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <Descriptions className="space-y-3">
                        <DescriptionsItem label="Assigned To">{intake.assigned_to || 'Unassigned'}</DescriptionsItem>
                        <DescriptionsItem label="Team">{intake.assigned_team || 'N/A'}</DescriptionsItem>
                        <DescriptionsItem label="Playbook">
                          {intake.playbook_id ? (
                            <Badge variant="secondary" className="bg-green-100 text-green-800">
                              {getPlaybookName(intake.playbook_id)}
                            </Badge>
                          ) : 'No playbook assigned'}
                        </DescriptionsItem>
                      </Descriptions>
                    </CardContent>
                  </Card>
                  
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-base">Attachments</CardTitle>
                    </CardHeader>
                    <CardContent>
                      {intake.attachments && intake.attachments.length > 0 ? (
                        <div className="space-y-2">
                          {intake.attachments.map((att, idx) => (
                            <div key={idx} className="flex items-center justify-between p-3 border rounded-lg">
                              <div className="flex items-center gap-3">
                                <FileText className="h-5 w-5 text-muted-foreground" />
                                <div>
                                  <p className="font-medium">{att.filename}</p>
                                  <p className="text-sm text-muted-foreground">{att.mime_type} • {Math.round(att.size / 1024)} KB</p>
                                </div>
                              </div>
                              <Button variant="outline" size="sm">
                                <Download className="h-3 w-3 mr-1" /> Download
                              </Button>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <Empty description="No attachments" />
                      )}
                    </CardContent>
                  </Card>
                </div>
              </TabsContent>
              
              <TabsContent value="source">
                <Card>
                  <CardContent className="p-4">
                    <pre className="max-h-[400px] overflow-auto bg-muted p-4 rounded text-xs font-mono">
                      {JSON.stringify(intake.source_metadata, null, 2)}
                    </pre>
                  </CardContent>
                </Card>
              </TabsContent>
              
              <TabsContent value="history">
                <Card>
                  <CardContent className="p-4">
                    <div className="space-y-6">
                      <div className="relative">
                        <div className="absolute left-4 top-0 bottom-0 w-0.5 bg-muted" />
                        {[
                          { label: 'Received', time: intake.received_at, content: `Email received via ${intake.channel} channel from ${intake.source_email}`, icon: Mail },
                          { label: 'Processing Started', time: intake.processing_started_at, content: 'Attachments extracted and stored', icon: File },
                          { label: 'Classified', time: intake.processing_completed_at, condition: intake.classified_type, content: `Type: ${intake.classified_type} | Counterparty: ${intake.classified_counterparty || 'Unknown'} | Confidence: ${Math.round((intake.confidence || 0) * 100)}%`, icon: Bot },
                          { label: 'Playbook Assigned', time: null, condition: intake.playbook_id, content: getPlaybookName(intake.playbook_id), icon: Shield },
                          { label: 'Assigned', time: null, condition: intake.assigned_to, content: `Assigned to user: ${intake.assigned_to}`, icon: Users },
                          { label: 'Routed', time: intake.processing_completed_at, condition: intake.status === 'routed', content: 'Intake fully processed and routed for review', icon: ArrowRight },
                          { label: 'Contract Created', time: null, condition: intake.created_contract_id, content: `Contract ID: ${intake.created_contract_id}`, icon: Database },
                          { label: 'Error', time: null, condition: intake.error_message, content: intake.error_message, icon: AlertCircle, error: true },
                        ].map((item, idx) => {
                          if (item.condition === false || item.condition === undefined) return null;
                          return (
                            <div key={idx} className="relative flex gap-4">
                              <div className="flex flex-col items-center">
                                <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm ${item.error ? 'bg-red-500 text-white' : 'bg-primary text-white'}`}>
                                  <item.icon className="h-4 w-4" />
                                </div>
                                {idx < 7 && <div className="w-0.5 h-16 bg-muted" />}
                              </div>
                              <div className="flex-1 pt-1">
                                <p className="font-medium">{item.label}</p>
                                <p className="text-sm text-muted-foreground">{item.content}</p>
                                {item.time && <p className="text-xs text-muted-foreground">{new Date(item.time).toLocaleString()}</p>}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </TabsContent>
            </Tabs>
          </div>
        </div>
      </Modal>
    );
  };

  // Process Modal
  const ProcessModal = () => {
    if (!selectedIntake) return null;
    
    return (
      <Modal open={processModalOpen} onOpenChange={setProcessModalOpen} className="max-w-2xl">
        <div className="flex flex-col">
          <div className="flex items-center justify-between border-b p-4">
            <div>
              <Modal.Title className="text-lg font-semibold">Process Intake</Modal.Title>
              <p className="text-sm text-muted-foreground">{selectedIntake.source_metadata?.subject || 'No Subject'}</p>
            </div>
            <Button variant="ghost" size="icon" onClick={() => setProcessModalOpen(false)}>
              <X className="h-4 w-4" />
            </Button>
          </div>
          
          <div className="p-4 space-y-6 overflow-y-auto max-h-[70vh]">
            <Alert className="bg-blue-50 border-blue-200">
              Processing will classify the contract, assign a playbook, and route to the appropriate team member.
            </Alert>
            
            <Form onSubmit={handleProcessSubmit}>
              <FormField
                control={{}}
                name="assign_to"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Assign To</FormLabel>
                    <FormControl>
                      <Select
                        value={field.value}
                        onValueChange={field.onChange}
                        placeholder="Select team member (or leave empty for auto-assign)"
                      >
                        <SelectTrigger className="w-full">
                          <SelectValue placeholder="Select team member..." />
                        </SelectTrigger>
                        <SelectContent>
                          {teamMembers.map(m => (
                            <SelectItem key={m.id} value={m.id}>
                              {m.firstName || ''} {m.lastName || ''} ({m.email})
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </FormControl>
                  </FormItem>
                )}
              />
              
              <FormField
                control={{}}
                name="playbook_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Playbook</FormLabel>
                    <FormControl>
                      <Select
                        value={field.value}
                        onValueChange={field.onChange}
                        placeholder="Select playbook (or leave empty for auto-select)"
                      >
                        <SelectTrigger className="w-full">
                          <SelectValue placeholder="Select playbook..." />
                        </SelectTrigger>
                        <SelectContent>
                          {playbooks.map(p => (
                            <SelectItem key={p.id} value={p.id}>
                              {p.name} v{p.version} ({p.contract_types?.join(', ') || 'All'})
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </FormControl>
                  </FormItem>
                )}
              />
              
              <Separator />
              
              <div className="space-y-4">
                <FormField
                  control={{}}
                  name="auto_create_contract"
                  render={({ field }) => (
                    <FormItem className="flex items-center gap-2">
                      <FormControl>
                        <Checkbox
                          checked={field.value}
                          onCheckedChange={field.onChange}
                        />
                      </FormControl>
                      <FormLabel>Auto-create contract record after processing</FormLabel>
                    </FormItem>
                  )}
                />
                
                <FormField
                  control={{}}
                  name="trigger_risk_assessment"
                  render={({ field }) => (
                    <FormItem className="flex items-center gap-2">
                      <FormControl>
                        <Checkbox
                          checked={field.value}
                          onCheckedChange={field.onChange}
                        />
                      </FormControl>
                      <FormLabel>Run automated risk scoring after contract creation</FormLabel>
                    </FormItem>
                  )}
                />
                
                <FormField
                  control={{}}
                  name="trigger_obligation_extraction"
                  render={({ field }) => (
                    <FormItem className="flex items-center gap-2">
                      <FormControl>
                        <Checkbox
                          checked={field.value}
                          onCheckedChange={field.onChange}
                        />
                      </FormControl>
                      <FormLabel>Extract post-signature obligations (for signed contracts)</FormLabel>
                    </FormItem>
                  )}
                />
              </div>
              
              <div className="flex justify-end gap-3 border-t pt-4">
                <Button variant="outline" onClick={() => setProcessModalOpen(false)}>Cancel</Button>
                <Button type="submit">
                  <ArrowRight className="h-4 w-4 mr-1" /> Process Intake
                </Button>
              </div>
            </Form>
          </div>
        </div>
      </Modal>
    );
  };

  // Config Modal
  const ConfigModal = () => (
    <Modal open={configModalOpen} onOpenChange={setConfigModalOpen} className="max-w-3xl max-h-[90vh]">
      <div className="flex flex-col h-full">
        <div className="flex items-center justify-between border-b p-4">
          <Modal.Title className="text-lg font-semibold">Resend Webhook Configuration</Modal.Title>
          <Button variant="ghost" size="icon" onClick={() => setConfigModalOpen(false)}>
            <X className="h-4 w-4" />
          </Button>
        </div>
        
        <div className="flex-1 overflow-y-auto p-4 space-y-6">
          <Alert className="bg-blue-50 border-blue-200">
            Configure your Resend webhook to enable zero-training email intake.
          </Alert>
          
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Webhook Endpoint</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <Descriptions className="space-y-3">
                <DescriptionsItem label="URL">
                  <div className="flex items-center gap-2 flex-wrap">
                    <code className="bg-muted px-2 py-1 rounded text-sm font-mono flex-1 min-w-0 break-all">
                      {window.location.origin}/api/v1/agentic/intake/webhook/resend
                    </code>
                    <Button variant="outline" size="icon" onClick={() => {
                      navigator.clipboard.writeText(`${window.location.origin}/api/v1/agentic/intake/webhook/resend`);
                      toast({ title: 'Copied', description: 'Webhook URL copied to clipboard' });
                    }}>
                      <Copy className="h-4 w-4" />
                    </Button>
                  </div>
                </DescriptionsItem>
                <DescriptionsItem label="Method"><code className="font-mono">POST</code></DescriptionsItem>
                <DescriptionsItem label="Content-Type"><code className="font-mono">application/json</code></DescriptionsItem>
                <DescriptionsItem label="Signature Header"><code className="font-mono">X-Resend-Signature</code> (HMAC-SHA256)</DescriptionsItem>
              </Descriptions>
            </CardContent>
          </Card>
          
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Expected Payload Format</CardTitle>
            </CardHeader>
            <CardContent>
              <pre className="max-h-[300px] overflow-auto bg-muted p-4 rounded text-xs font-mono">
{`{
  "type": "email.received",
  "created_at": "2024-01-15T10:30:00.000Z",
  "data": {
    "id": "msg_abc123",
    "from": "sender@example.com",
    "to": ["contracts@yourcompany.lexisense.app"],
    "subject": "New NDA for Review",
    "text": "Please find attached...",
    "html": "<p>Please find attached...</p>",
    "attachments": [
      {
        "filename": "NDA.pdf",
        "content_type": "application/pdf",
        "content": "base64-encoded-content..."
      }
    ]
  }
}`}
              </pre>
            </CardContent>
          </Card>
          
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Setup Instructions</CardTitle>
            </CardHeader>
            <CardContent>
              <ol className="list-decimal list-inside space-y-3 text-sm text-muted-foreground">
                <li>Go to <a href="https://resend.com/domains" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">Resend Dashboard → Domains</a></li>
                <li>Select your domain and click "Webhooks"</li>
                <li>Add new webhook with the URL above</li>
                <li>Select "email.received" event</li>
                <li>Set webhook secret in environment variable: <code className="bg-muted px-1 rounded">RESEND_WEBHOOK_SECRET</code></li>
                <li>Configure intake email address (e.g., <code className="bg-muted px-1 rounded">contracts@yourcompany.lexisense.app</code>)</li>
              </ol>
            </CardContent>
          </Card>
          
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Test Webhook</CardTitle>
            </CardHeader>
            <CardContent>
              <Button 
                variant="outline"
                onClick={async () => {
                  try {
                    const token = localStorage.getItem('token');
                    const res = await fetch('/api/agentic/intake/test-webhook', {
                      method: 'POST',
                      headers: { 
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${token}`
                      }
                    });
                    const data = await res.json();
                    if (data.success) {
                      toast({ title: 'Success', description: 'Test webhook sent' });
                    } else {
                      toast({ title: 'Error', description: data.error || 'Failed to send test webhook', variant: 'destructive' });
                    }
                  } catch (e) {
                    toast({ title: 'Error', description: 'Failed to send test webhook', variant: 'destructive' });
                  }
                }}
              >
                <Send className="h-4 w-4 mr-1" /> Send Test Webhook
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </Modal>
  );

  return (
    <Layout>
      <div className="space-y-6" data-testid="intake-dashboard-page">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold">Business Intake Dashboard</h1>
            <p className="text-muted-foreground mt-1">Zero-training contract intake via email - powered by Resend webhooks & AI classification</p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setConfigModalOpen(true)}>
              <Settings className="h-4 w-4 mr-1" /> Webhook Config
            </Button>
            <Button variant="outline" onClick={handleRetryFailed}>
              <RefreshCw className="h-4 w-4 mr-1" /> Retry Failed
            </Button>
            <Button onClick={fetchIntakes} disabled={loading}>
              <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
            </Button>
          </div>
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
          <StatCard title="Total Intakes" value={intakes.length} icon={Mail} color="blue-500" />
          <StatCard title="Pending Processing" value={intakes.filter(i => ['received', 'processing'].includes(i.status)).length} icon={Clock} color="yellow-500" />
          <StatCard title="Classified & Routed" value={intakes.filter(i => ['classified', 'routed'].includes(i.status)).length} icon={CheckCircle} color="green-500" />
          <StatCard title="Contracts Created" value={intakes.filter(i => i.created_contract_id).length} icon={Database} color="purple-500" />
          <StatCard title="Failed" value={intakes.filter(i => i.status === 'failed').length} icon={AlertCircle} color="red-500" />
        </div>

        {/* Filters */}
        <Card>
          <CardContent className="p-4">
            <div className="flex flex-col sm:flex-row gap-4">
              <Select
                value={filters.status}
                onValueChange={handleStatusFilter}
              >
                <SelectTrigger className="w-[180px]">
                  <SelectValue placeholder="All Statuses" />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(statusLabels).map(([value, label]) => (
                    <SelectItem key={value} value={value}>{label}</SelectItem>
                  ))}
                </SelectContent              </Select>
              
              <div className="flex-1 max-w-md">
                <Input
                  placeholder="Search sender, subject, counterparty..."
                  value={filters.search}
                  onChange={(e) => handleSearch(e.target.value)}
                  className="w-full"
                />
              </div>
              
              <div className="flex-1 max-w-md">
                <Popover>
                  <PopoverTrigger asChild>
                    <Button variant="outline" className="w-full justify-start">
                      <Calendar className="h-4 w-4 mr-2" />
                      <span>Date Range</span>
                      <ChevronDown className="h-4 w-4 ml-auto" />
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-80 p-4">
                    <Calendar
                      mode="range"
                      selected={filters.dateRange}
                      onSelect={handleDateRangeChange}
                      numberOfMonths={2}
                    />
                  </PopoverContent>
                </Popover>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Intake Table */}
        <Card>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[200px]">Sender</TableHead>
                    <TableHead className="w-[250px]">Subject</TableHead>
                    <TableHead className="w-[130px]">Status</TableHead>
                    <TableHead className="w-[150px]">Type</TableHead>
                    <TableHead className="w-[150px]">Counterparty</TableHead>
                    <TableHead className="w-[100px]">Confidence</TableHead>
                    <TableHead className="w-[120px]">Attachments</TableHead>
                    <TableHead className="w-[120px]">Assigned To</TableHead>
                    <TableHead className="w-[150px]">Playbook</TableHead>
                    <TableHead className="w-[150px]">Contract</TableHead>
                    <TableHead className="w-[160px]">Received</TableHead>
                    <TableHead className="w-[200px]">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {loading ? (
                    [...Array(5)].map((_, i) => (
                      <TableRow key={i}>
                        {[...Array(12)].map((_, j) => (
                          <TableCell key={j}><Skeleton className="h-4 w-full" /></TableCell>
                        ))}
                      </TableRow>
                    ))
                  ) : intakes.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={12} className="text-center py-12">
                        <Empty description="No intakes found" />
                      </TableCell>
                    </TableRow>
                  ) : (
                    intakes.map((record) => (
                      <TableRow key={record.id}>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <Avatar className="h-8 w-8 bg-blue-100">
                              <AvatarFallback>
                                <Mail className="h-4 w-4 text-blue-600" />
                              </AvatarFallback>
                            </Avatar>
                            <span className="font-medium truncate max-w-[150px]">{record.source_email}</span>
                          </div>
                        </TableCell>
                        <TableCell>
                          <span className="truncate block max-w-[230px]">{record.source_metadata?.subject || 'No subject'}</span>
                        </TableCell>
                        <TableCell>{renderStatusBadge(record.status)}</TableCell>
                        <TableCell>
                          {record.classified_type ? (
                            <Badge variant="secondary" className="bg-blue-100 text-blue-800">{record.classified_type}</Badge>
                          ) : <span className="text-muted-foreground">—</span>}
                        </TableCell>
                        <TableCell>
                          <span className="truncate block max-w-[130px]">{record.classified_counterparty || '—'}</span>
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <Progress value={Math.round((record.confidence || 0) * 100)} className="w-[60px] h-2" />
                            <span className="text-sm font-medium">{Math.round((record.confidence || 0) * 100)}%</span>
                          </div>
                        </TableCell>
                        <TableCell>{renderAttachments(record.attachments)}</TableCell>
                        <TableCell>
                          {record.assigned_to ? (
                            <Avatar className="h-8 w-8">
                              <AvatarFallback>{record.assigned_to.charAt(0).toUpperCase()}</AvatarFallback>
                            </Avatar>
                          ) : (
                            <Badge variant="outline" className="text-muted-foreground">Unassigned</Badge>
                          )}
                        </TableCell>
                        <TableCell>
                          {record.playbook_id ? (
                            <Badge variant="secondary" className="bg-green-100 text-green-800">
                              {getPlaybookName(record.playbook_id)}
                            </Badge>
                          ) : <span className="text-muted-foreground">—</span>}
                        </TableCell>
                        <TableCell>
                          {record.created_contract_id ? (
                            <Button variant="ghost" size="sm" onClick={() => window.location.href = `/contracts/${record.created_contract_id}`}>
                              <FileText className="h-3 w-3 mr-1" /> View
                            </Button>
                          ) : <span className="text-muted-foreground">—</span>}
                        </TableCell>
                        <TableCell>
                          {record.received_at ? new Date(record.received_at).toLocaleString() : '-'}
                        </TableCell>
                        <TableCell>
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button variant="ghost" size="icon" className="h-8 w-8">
                                <ChevronDown className="h-4 w-4" />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem onClick={() => handleViewIntake(record)}>
                                <Eye className="h-4 w-4 mr-2" /> View Details
                              </DropdownMenuItem>
                              {record.status !== 'routed' && record.status !== 'failed' && (
                                <DropdownMenuItem onClick={() => handleProcessIntake(record)}>
                                  <ArrowRight className="h-4 w-4 mr-2" /> Process
                                </DropdownMenuItem>
                              )}
                              {record.status === 'routed' && !record.created_contract_id && (
                                <DropdownMenuItem onClick={() => handleCreateContract(record.id)}>
                                  <Database className="h-4 w-4 mr-2" /> Create Contract
                                </DropdownMenuItem>
                              )}
                              {record.status === 'failed' && (
                                <DropdownMenuItem onClick={() => handleProcessIntake(record)} className="text-red-600">
                                  <RefreshCw className="h-4 w-4 mr-2" /> Retry
                                </DropdownMenuItem>
                              )}
                              <DropdownMenuSeparator />
                              <DropdownMenuItem>
                                <FileText className="h-4 w-4 mr-2" /> View Contract
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>
            
            {/* Pagination */}
            <div className="border-t p-4 flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                Showing {((pagination.current - 1) * pagination.pageSize) + 1} to {Math.min(pagination.current * pagination.pageSize, pagination.total)} of {pagination.total} results
              </p>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => setPagination(p => ({ ...p, current: p.current - 1 }))} disabled={pagination.current <= 1}>
                  Previous
                </Button>
                <Button variant="outline" size="sm" onClick={() => setPagination(p => ({ ...p, current: p.current + 1 }))} disabled={pagination.current * pagination.pageSize >= pagination.total}>
                  Next
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        <DetailModal />
        <ProcessModal />
        <ConfigModal />
      </div>
    </Layout>
  );
}